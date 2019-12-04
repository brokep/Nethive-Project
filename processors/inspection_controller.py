import socket
import threading
import json
from storage.redistor import RedisClient
from dateutil.parser import parse
import traceback
import time
import os
from utils import decode_deeply
import base64
from processors import xss_watcher
from processors import sql_inspector

SQL_METHOD_LESS_INTEREST = ["USE"]

# import signal

AUDIT_CONTROL_HOST = os.getenv("AUDIT_CONTROL_HOST")
AUDIT_CONTROL_PORT = int(os.getenv("AUDIT_CONTROL_PORT"))

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((AUDIT_CONTROL_HOST, AUDIT_CONTROL_PORT))
server.listen(5)

redis = RedisClient.getInstance()

def keyboardInterruptHandler(signal, frame):
    server.close()

def parse_returned_sql_rows(raw_rows):
    raw_rows = list(filter(None, raw_rows.split("\n")))
    headers = raw_rows[0].split(",")
    responses = []
    for row in raw_rows[1:]:
        rsp_obj = {}
        row_split = row.split(",")
        for i in range(len(headers)):
            rsp_obj[headers[i]] = normalize_json_quote_problem(row_split[i])
        responses.append(rsp_obj)
    return responses

def parse_mysql_beat_packet(beat_packet):
    try:     
        if beat_packet['method'] in SQL_METHOD_LESS_INTEREST:
            return
        d = parse(beat_packet['@timestamp'])
        parsed_packet = {
            # 'timestamp': int(time.mktime(d.timetuple())),
            'conn_stat': {
                'from_ip': beat_packet['client']['ip'],
                'from_port': beat_packet['client']['port'],
                'bytes': beat_packet['client']['bytes'],
            },
            'sql_method': beat_packet['method'],
            'sql_data': {
                'query': beat_packet['query'],
            },
            'sql_stat': {
                'affected_rows': beat_packet['mysql']['affected_rows'],
                'insert_id': beat_packet['mysql']['insert_id'],
                'num_fields': beat_packet['mysql']['num_fields'],
                'num_rows': beat_packet['mysql']['num_rows'], 
            },
            'status': beat_packet['status']
        }

        if 'path' in beat_packet:
            parsed_packet['db_object']: beat_packet['path']

        if 'error_code' in beat_packet['mysql']:
            parsed_packet['sql_stat']['error_code'] = beat_packet['mysql']['error_code']
            parsed_packet['sql_stat']['error_message'] = beat_packet['mysql']['error_message']
            parsed_packet['sql_data']['response'] = []
        else:
            parsed_packet['sql_data']['response'] = parse_returned_sql_rows(beat_packet['response'])

        return parsed_packet

    except Exception as e:
        print("[Inspection Controller] %s" % e)
        pass
        # print(traceback.format_exc())

def restructure_for_auditor(package):
    try:
        package = {
            "req_packet": package['req_packet'],
            "res_body": package['res_body'].encode(),
            "time": int(time.time())
        }
    except Exception as e:
        print("[Inspection Controller] %s" % e)
        # print(traceback.format_exc())
    return package

def create_xss_audit_package(package, parsed_sql_data):
    try:
        package = restructure_for_auditor(package)
        package['req_packet']['sql_data'] = parsed_sql_data['sql_data']
        package['req_packet']['sql_stat'] = parsed_sql_data['sql_stat']
    except Exception as e:
        print("[Inspection Controller] %s" % e)
        # print(traceback.format_exc())
    return package

def create_sqli_inspection_package(package, parsed_sql_data):
    package = restructure_for_auditor(package)
    package['sql_response'] = parsed_sql_data

    return package

def get_flow_time_average():
    # TODO: write an algorithm to calculate time between http and sql dynamically
    return 1.0

def normalize_json_quote_problem(the_data):
    """
        Setiap data yang mengandung double-quote (") pada database, ketika masuk ke packetbeat, double-quote tersebut akan menjadi double double-quote (""). Fungsi ini berguna untuk melakukan normalisasi terhadap anomali tersebut.

        Algorithm:
        1. remove wrapping double quote (at start and end of the data)
        2. for each of the remaining double quotes, remove half of it.
            Example:
                "nama saya adalah """"""ANDO""""""" | 6 double quotes before ANDO (exclude the double quotes in the beginning of the string), 7 double quotes after ANDO
                1 -> nama saya adalah """"""ANDO"""""" | 6 double quotes before ANDO, 6 double quotes after ANDO
                2 -> nama saya adalah \"\"\"ANDO\"\"\" | 6/2 double quotes before and after ANDO (remove the backslashes)
    """
    # --1.
    if the_data.startswith('"') and the_data.endswith('"'):
        the_data = the_data[1:-1]
        # --2.
        the_data = the_data.replace('""', '"')
    return the_data

# def find_related_redis_data(package_identifiers):
#     for the_pack in package_identifiers:
#         timestamp, package_id = the_pack
#         package_id =  int(float(package_id))
#         package = redis.rs_get_all_pop_one("audit:{}".format(package_id))
#         yield package

def unwrap_http_bundle(bundle_package):
    for k in bundle_package:
        redis_key = k
        bundle_packed = json.loads(base64.decodestring(bundle_package[k][0]['package'].encode("utf-8")))
        return (redis_key, bundle_packed)
        
def handle_client_connection(client_socket):
    while True:
        request = client_socket.recv(16384)

        if not request:
            print("[Inspection Controller] Client has disconnected")
            client_socket.close()
            break
        try:
            lower_boundary = int(int(time.time()) - get_flow_time_average())
            upper_boundary = int(int(time.time()) + get_flow_time_average())
            http_bundles = redis.ts_get_http_bundles(lower_boundary, upper_boundary)
            # package_identifiers = redis.ts_get_by_range(RedisClient.TS_STORE_KEY, lower_boundary, upper_boundary)
            # print(package_identifiers.__dict__)

            raw_inspection_data = json.loads(request.decode("utf-8"))

            if raw_inspection_data['type'] == 'mysql':
                # print("BEFORE", raw_inspection_data)
                parsed_sql_data = parse_mysql_beat_packet(raw_inspection_data)
                # print("AFTER", parsed_sql_data)
                if parsed_sql_data:
                    for bundle in http_bundles:
                        redis_key, bundle_packed = unwrap_http_bundle(bundle)

                        # delete the data to prevent rechecking
                        delete_status = redis.ts_expire_http_bundle(redis_key)
                        if delete_status:
                            print("Bundle {} Deleted!".format(redis_key))

                        deep_xss_package = create_xss_audit_package(decode_deeply(bundle_packed), parsed_sql_data)
                        deep_sqli_package = create_sqli_inspection_package(decode_deeply(bundle_packed), parsed_sql_data)

                        # print("XSS PACKAGE", deep_xss_package)
                        # print("SQLI PACKAGE", deep_sqli_package)

                        sql_inspection = threading.Thread(target=sql_inspector.inspect, args=(deep_sqli_package,)) # inspect request to find sqli

                        # print("DATA TO CHECK: ", len(http_bundles))

                        xss_audit = threading.Thread(target=xss_watcher.domparse, args=(deep_xss_package, False,)) # inspect request data ALONG WITH sql response

                        sql_inspection.start()
                        xss_audit.start()

                        # print("Bottom!")

            elif raw_inspection_data['type'] == 'http':
                light_package = restructure_for_auditor(raw_inspection_data['package'])

                xss_audit = threading.Thread(target=xss_watcher.domparse, args=(light_package, False,)) # inspect request data WITHOUT sql response
                xss_audit.start()
                
        except Exception as e:
            print("[Inspection Controller] %s" % e)    
            # print(request.decode("utf-8"))
            # print(traceback.format_exc())
            pass

def start():
    # --- Set signal handlers
    # signal.signal(signal.SIGINT, keyboardInterruptHandler)

    while True:
        client_sock, address = server.accept()
        print('[Inspection Controller] Accepted connection from {}:{}'.format(address[0], address[1]))
        client_handler = threading.Thread(
            target=handle_client_connection,
            args=(client_sock,)
        )
        client_handler.start()

    server.close()

def run():
    print('[Inspection Controller] Listening on {}:{}'.format(AUDIT_CONTROL_HOST, AUDIT_CONTROL_PORT))
    start()
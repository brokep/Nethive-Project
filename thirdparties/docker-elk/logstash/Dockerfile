ARG ELK_VERSION

# https://github.com/elastic/logstash-docker
FROM docker.elastic.co/logstash/logstash:${ELK_VERSION}

# Add your logstash plugins setup here
# Example: RUN logstash-plugin install logstash-filter-json
RUN logstash-plugin install logstash-patterns-core
RUN logstash-plugin install logstash-filter-json
RUN logstash-plugin install logstash-filter-grok
RUN logstash-plugin install logstash-filter-aggregate
RUN logstash-plugin install logstash-output-tcp
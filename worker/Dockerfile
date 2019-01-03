FROM debian:stretch

RUN apt-get update && \
  apt-get install -y --no-install-recommends python3-pip && \
  useradd -ms /bin/bash utbot

USER utbot
WORKDIR /home/utbot
RUN pip3 install celery && ln -s /ute ute

COPY ../x265/x265 /usr/local/bin/x265
ENV PATH="/home/utbot/.local/bin:${PATH}"

## will be replaced with a more specific COMMAND 
## directive in docker-compose.yml
ENTRYPOINT [ "celery" ]

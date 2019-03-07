#!/bin/bash

## run from root of UTE folder

openssl req \
  -newkey rsa:2048 \
  -nodes \
  -sha256 \
  -keyout resources/registry/certs/domain.key \
  -x509 \
  -days 3650 \
  -out resources/registry/certs/domain.crt

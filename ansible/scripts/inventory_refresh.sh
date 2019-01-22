#!/bin/bash

if ! [[ -f "$HOME/.local/bin/vagranttoansible" ]]; then
  echo "Installing vagranttoansible via pip"
  `pip3 install vagranttoansible`
fi

vagranttoansible -o ./hosts.ini

tmp=$(< ./hosts.ini)
groupName='[nodes]'

`echo [nodes] > tmpFile`
`cat hosts.ini >> tmpFile`
`mv tmpFile hosts.ini`


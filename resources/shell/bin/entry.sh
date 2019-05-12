#!/bin/bash

## makes any created file 'rw' in the host filesystem
umask 0000

## set container root password to 'password'
echo root:password | chpasswd

## become user 'utbot'
echo "Becoming user 'utbot'"
echo "Root password is 'password'"
su utbot

/bin/bash -i

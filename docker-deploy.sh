#!/bin/bash

HOST_IP=$(hostname -I | awk '{print $1}')
if [ "${1}" ]; then
    TARGET_IP=$1
else
    TARGET_IP=$HOST_IP
fi

if nc -zw3 "${TARGET_IP}" 2376; then
    export DOCKER_HOST="tcp://${TARGET_IP}:2376"
    export DOCKER_TLS_VERIFY=1

    if [ ! -d cert ]; then
        mkdir cert
    fi

    if [[ -f ca.pem && -f cert.pem && -f key.pem ]]; then
        cp {ca,cert,key}.pem cert/
    fi

    if [[ -f cert/ca.pem && -f cert/cert.pem && -f cert/key.pem ]]; then
        if [ ! -d ~/.docker ]; then
            mkdir ~/.docker
        fi
        cp cert/{ca,cert,key}.pem ~/.docker/
    else
        echo "cert files not found"
    fi
else
    echo "TARGET_IP is closed"
fi

if docker service ls | grep -wq "codedu_terminal"; then
    echo "updating codedu_terminal"
    docker pull ctmanjak/codedu_terminal
    docker service update codedu_terminal --force --image ctmanjak/codedu_terminal
fi
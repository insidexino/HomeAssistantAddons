ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8

# Copy data for add-on
COPY run.sh master.py /

# Install requirements for add-on
RUN apk add --no-cache python3 py3-pip && \
        python3 -m pip install pyserial && \
        python3 -m pip install paho-mqtt

WORKDIR /share

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
FROM restic/restic:0.17.3

RUN apk add --no-cache bash postgresql-client curl

COPY backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["/bin/bash"]
CMD ["/usr/local/bin/backup.sh", "--daemon"]

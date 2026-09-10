# Pinned unmodified go-livepeer e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6.
# The upstream OCI index contains linux/amd64 plus provenance, not an arm64 image.
FROM livepeer/go-livepeer@sha256:ea7a9434e66ae328711c3324ca58536dc7e85da0ab43c02a6550d58ac7f9d8b6

USER root
RUN mkdir -p /data && chown 10001:10001 /data
COPY --chmod=0555 deploy/signer/entrypoint.sh /usr/local/bin/clearinghouse-signer
USER 0:0
EXPOSE 8935
ENTRYPOINT ["/usr/local/bin/clearinghouse-signer"]

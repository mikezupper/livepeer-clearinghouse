# Pinned unmodified go-livepeer v0.9.2 release image.
# The upstream OCI index contains linux/amd64 plus provenance, not an arm64 image.
FROM livepeer/go-livepeer@sha256:972870bed3f302ee69ada1e719d7767274a77d2e752be1ab82581ddf2365dfe7

USER root
RUN mkdir -p /data && chown 10001:10001 /data
COPY --chmod=0555 deploy/signer/entrypoint.sh /usr/local/bin/clearinghouse-signer
USER 0:0
EXPOSE 8935
ENTRYPOINT ["/usr/local/bin/clearinghouse-signer"]

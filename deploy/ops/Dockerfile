# syntax=docker/dockerfile:1.7
ARG POSTGRES_IMAGE=postgres:18.6-bookworm@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af

FROM ${POSTGRES_IMAGE} AS age-amd64
ADD --checksum=sha256:cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10 \
  https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz \
  /tmp/age.tar.gz
RUN mkdir /out && tar -xzf /tmp/age.tar.gz -C /tmp \
    && cp /tmp/age/age /tmp/age/age-keygen /out/

FROM ${POSTGRES_IMAGE} AS age-arm64
ADD --checksum=sha256:6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4 \
  https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-arm64.tar.gz \
  /tmp/age.tar.gz
RUN mkdir /out && tar -xzf /tmp/age.tar.gz -C /tmp \
    && cp /tmp/age/age /tmp/age/age-keygen /out/

FROM age-${TARGETARCH} AS age

FROM ${POSTGRES_IMAGE} AS runtime
RUN groupadd --gid 10001 clearinghouse-ops \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent clearinghouse-ops
COPY --from=age --chmod=0555 --chown=0:0 /out/age /out/age-keygen /usr/local/bin/
COPY --chmod=0555 --chown=0:0 deploy/ops/entrypoint.sh deploy/ops/database.sh /usr/local/lib/clearinghouse-ops/
USER 0:0
ENTRYPOINT ["/usr/local/lib/clearinghouse-ops/entrypoint.sh"]
CMD ["status"]

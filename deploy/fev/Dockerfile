# syntax=docker/dockerfile:1.7
# Immutable migration runtimes retain the installed PDF tools / OCR dependencies.
# Their image IDs are checked by deploy-handlepdf before every build.
ARG CORE_RUNTIME=fev/handlepdf-core-runtime:20261117
ARG OCR_RUNTIME=fev/handlepdf-ocr-runtime:20261117
FROM gradle:9.6.0-jdk25@sha256:e3905233ae349e72016daf8a0e19f085a1dd89ded8ec88b3d8335d3fd0b350f4 AS sdk
FROM node:22.22.1-bookworm-slim@sha256:4f77a690f2f8946ab16fe1e791a3ac0667ae1c3575c3e4d0d4589e9ed5bfaf3d AS node
FROM ${CORE_RUNTIME} AS compiler
USER root
ENTRYPOINT []
COPY --from=sdk /opt/java/openjdk /opt/jdk
COPY --from=sdk /opt/gradle /opt/gradle
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
ADD --checksum=sha256:4e7d24f1bf38218aec8f244eb7ba671f898830f9f87b3c9b30ff1c09e3135576 https://github.com/go-task/task/releases/download/v3.49.1/task_linux_amd64.tar.gz /tmp/task.tar.gz
RUN tar -xzf /tmp/task.tar.gz -C /usr/local/bin task && rm /tmp/task.tar.gz && ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
ENV JAVA_HOME=/opt/jdk PATH=/opt/jdk/bin:/opt/gradle/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    GRADLE_USER_HOME=/gradle-cache STIRLING_FLAVOR=core DISABLE_ADDITIONAL_FEATURES=true \
    GRADLE_OPTS="-Dorg.gradle.daemon=false -Dorg.gradle.workers.max=2 -Dorg.gradle.jvmargs=-Xmx2g" \
    NODE_OPTIONS=--max-old-space-size=1536 RAYON_NUM_THREADS=2 \
    JDK_JAVA_OPTIONS="--add-exports=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED --add-exports=jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED --add-exports=jdk.compiler/com.sun.tools.javac.parser=ALL-UNNAMED --add-exports=jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED --add-exports=jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED -XX:ActiveProcessorCount=2"
WORKDIR /build
COPY . .
# Same Gradle 9.6.0 as gradle-wrapper.properties, already present in official SDK.
# Task's backend gate uses this launcher rather than re-downloading the distribution.
RUN printf '#!/bin/sh\nexec /opt/gradle/bin/gradle "$@"\n' > gradlew && chmod +x gradlew
FROM compiler AS checks
ARG DEPLOY_SHA=unknown
LABEL com.fev.app=handlepdf org.opencontainers.image.revision=$DEPLOY_SHA
RUN --mount=type=cache,id=fev-handlepdf-gradle,target=/gradle-cache task backend:check
FROM compiler AS app-build
RUN --mount=type=cache,id=fev-handlepdf-gradle,target=/gradle-cache --mount=type=cache,id=fev-handlepdf-npm,target=/root/.npm \
    gradle clean build -PbuildWithFrontend=true -PjpdfiumPlatforms=linux-x64 -PprototypesMode=false -x spotlessApply -x spotlessCheck -x test -x sonarqube --no-daemon
FROM app-build AS layers
RUN java -Djarmode=tools -jar app/core/build/libs/*.jar extract --layers --destination /layers
FROM ${CORE_RUNTIME} AS core
USER root
ARG DEPLOY_SHA=unknown
LABEL com.fev.app=handlepdf org.opencontainers.image.revision=$DEPLOY_SHA
RUN rm -rf /app/BOOT-INF /app/META-INF /app/org /app/lib /app/app.jar /app.jar
COPY --from=layers --chown=1000:1000 /layers/dependencies/ /app/
COPY --from=layers --chown=1000:1000 /layers/spring-boot-loader/ /app/
COPY --from=layers --chown=1000:1000 /layers/snapshot-dependencies/ /app/
COPY --from=layers --chown=1000:1000 /layers/application/ /app/
COPY --from=app-build --chown=1000:1000 /build/build/libs/restart-helper.jar /restart-helper.jar
COPY --chown=1000:1000 scripts/ /scripts/
RUN set -eu; set -- /app/stirling-pdf-*.jar; test "$#" = 1; mv "$1" /app/app.jar; chmod +x /scripts/*
ENV VERSION_TAG=fev-production
FROM ${OCR_RUNTIME} AS ocr
ARG DEPLOY_SHA=unknown
LABEL com.fev.app=handlepdf org.opencontainers.image.revision=$DEPLOY_SHA
COPY --chown=10001:10001 integrations/paddleocr-table/app/ /app/app/
FROM ocr AS ocr-checks
COPY --chown=10001:10001 integrations/paddleocr-table/tests/ /app/tests/
ENTRYPOINT ["python", "-m", "unittest", "discover", "-s", "/app/tests", "-v"]

# The alerts service. docker-compose.yml and the pipeline build it from this folder:
#     docker build -t alerts services/alerts

# The exact Python image, by version and by digest, so every build starts from
# the same files. The digest covers both Intel and ARM machines.
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# Log lines reach stdout at once, and Python leaves no .pyc files behind.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

# Dependencies first. Docker reuses this layer until requirements.txt changes,
# so a code change does not reinstall them. Every version in the file is pinned.
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

# Only what the service needs to run, named one by one. There is no "COPY . .",
# so tests, caches and a local .env can never end up in the image.
COPY app.py .
COPY app ./app
COPY migrations ./migrations

# Run as a user with no rights beyond reading these files. The service writes
# nothing to disk: its data is in SQL Server and its logs go to stdout.
RUN useradd --system --no-create-home --uid 10001 service
USER 10001

# The commit this image was built from. GET /health reports it as "version".
# It comes last, so a new commit does not rebuild the layers above.
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

# The default port. PORT changes it, and every other setting is an environment
# variable too. No secret is set here: they are given to the container at run time.
EXPOSE 8002
CMD ["python", "app.py"]

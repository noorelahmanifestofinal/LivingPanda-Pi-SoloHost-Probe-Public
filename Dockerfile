FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html /app/
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()" || exit 1
CMD ["python", "/app/server.py"]

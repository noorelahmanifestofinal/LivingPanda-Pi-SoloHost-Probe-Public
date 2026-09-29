FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py /app/
EXPOSE 8001
HEALTHCHECK --interval=20s --timeout=4s --start-period=8s --retries=4 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=2).read()" || exit 1
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8001"]

FROM python:3.12-slim
WORKDIR /app
COPY bose_radio.py ./
COPY soundtouch/ ./soundtouch/
COPY web/ ./web/
EXPOSE 8077
CMD ["python3", "-u", "bose_radio.py", "serve"]

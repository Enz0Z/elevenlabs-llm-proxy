@ECHO OFF

del elevenlabs-llm-proxy.tar

docker build -t elevenlabs-llm-proxy .

docker save -o elevenlabs-llm-proxy.tar elevenlabs-llm-proxy
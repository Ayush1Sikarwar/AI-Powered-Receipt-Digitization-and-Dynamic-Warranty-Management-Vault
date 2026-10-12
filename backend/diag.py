import httpx
payload = {
    "model": "llama3.1:8b",
    "max_tokens": 1024,
    "messages": [
        {"role": "system", "content": "Return ONLY this JSON: {\"test\": \"ok\"}"},
        {"role": "user", "content": "hello"},
    ],
}
resp = httpx.post("http://127.0.0.1:11434/v1/chat/completions", json=payload, timeout=90)
print("status:", resp.status_code)
print("body:", resp.text)

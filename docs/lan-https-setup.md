# Phase 8 LAN HTTPS via Nginx

Ember continues serving HTTP on fixed port 8787. An explicitly configured trusted Nginx proxy terminates HTTPS and forwards browser requests and LiveKit signalling. LiveKit retains its existing configured TCP/UDP media ports. The proxy must overwrite Host and X-Forwarded-Proto; Ember trusts those only when the TCP peer matches the configured proxy IP. Public-domain requests from other peers fail. The existing loopback activation remains in place.

With the explicit proxy configuration, opening the public origin grants the existing full operator session without an activation link. Anyone able to reach the Nginx endpoint has that authority, including access to private memory and controls. Restrict Nginx access to intended clients. One human participant at a time remains supported. No certificates, DNS, firewall rules, services, memory or gateway settings are modified by startup.

Acceptance requires tests for proxy identity, host and origin validation, session cookie, local activation, WSS endpoint and degraded behavior. The active Nginx HTTPS page, authenticated state and room-token endpoints have been checked; a WebSocket room join, browser microphone, client trust and media connectivity require end-to-end validation.

## Configuring the existing Nginx server

Substitute your Ember LAN address, Nginx peer address and HTTPS hostname for the examples below.

In Ember's deployment TOML, set `browser.enabled = true`, `browser.endpoint = "http://127.0.0.1:8787"`, `browser.lan_address = "10.254.254.11"`, `browser.public_origin = "https://ember.example.test"`, and `browser.trusted_proxy = "10.254.254.10"`. LAN mode requires managed IPv4 LiveKit. Use the current host address if it changes. These settings take effect after an authorized restart.

On the existing Nginx HTTPS virtual host for `ember.example.test`, retain its certificate and restrict access to the intended LAN. Replace or adapt its locations with the following; the proxy must connect directly from 192.168.10.2. Nginx must overwrite the two headers shown rather than accepting them from clients. Preserve any existing TLS and access controls.

```nginx
location /rtc {
    proxy_pass http://10.254.254.11:7880;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}

location / {
    proxy_pass http://10.254.254.11:8787;
    proxy_http_version 1.1;
    proxy_set_header Host ember.example.test;
    proxy_set_header X-Forwarded-Proto https;
}
```

The certificate must be trusted on each client, and `ember.example.test` must resolve to Nginx. Browser signalling passes through Nginx; LiveKit media still needs network access from the client to Ember's configured TCP and UDP media ports (defaults 7881 and 7882). Validate the Nginx configuration with `nginx -t` before reloading it. Browser port 8787 is fixed by the proxy-mode configuration; external HTTPS normally uses 443. The service does not modify Nginx or restart it.

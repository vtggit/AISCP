# The planner screen: static pages served by the official non-root nginx (uid 101, port 8080,
# pid and cache under /tmp, so it runs on a read-only root filesystem).
FROM nginxinc/nginx-unprivileged:1.25-alpine@sha256:8265b1df5a89cc1a0a067e472bf47aca7cee52f0561c98a0dff91312dcdd8adb

# An ALLOWLIST, never `COPY .`: the build context also holds nginx.conf and the Playwright
# suite, and neither belongs in the web root. A new runtime asset directory must be added
# here; forgetting it fails loudly with a 404 instead of publishing something by accident.
COPY index.html /usr/share/nginx/html/
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080
USER 101

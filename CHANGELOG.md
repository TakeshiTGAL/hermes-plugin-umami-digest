# Changelog

## 0.1.0

Morning no-agent digest of yesterday's pageviews, visitors, day-over-day change, top five pages by pageviews, and a 2.0x surge flag from a self-hosted Umami instance. Expanded paths are read 500 at a time in visitor order. Offset walks until the list ends or 20 pages. A capped day says the five are the top 5 among the first N paths. When the previous day has 0 pageviews, the surge line says new traffic and does not quote the 2.0x threshold. `hermes umami-digest schedule` writes `umami-digest.py` and creates the job with `hermes cron create --no-agent --script`. The default schedule is `0 8 * * *`. `umami_digest` returns the same letter. Secrets are `UMAMI_URL`, `UMAMI_API_KEY`, and `UMAMI_WEBSITE_ID` through `agent.secret_scope.get_secret`.

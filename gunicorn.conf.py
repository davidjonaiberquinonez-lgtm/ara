import multiprocessing

bind = "0.0.0.0:5000"
workers = 4
worker_class = "gevent"
worker_connections = 64
timeout = 120
keepalive = 5
graceful_timeout = 30
max_requests = 10000
max_requests_jitter = 1000
accesslog = "-"
errorlog = "-"
loglevel = "info"

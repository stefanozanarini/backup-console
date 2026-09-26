#!/bin/sh
# Ricarica nginx ogni 6 ore per prendere i certificati rinnovati da certbot.
(while :; do sleep 21600; nginx -s reload; done) &

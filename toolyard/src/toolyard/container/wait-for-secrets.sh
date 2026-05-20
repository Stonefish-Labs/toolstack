while [ ! -f /run/secrets/.ready ]; do
  sleep 0.05
done

exec "$@"

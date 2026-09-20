#!/bin/sh

if [ $# -ne 0 ]; then
    exec /notify_irc.py "$@"
fi

if [ "$INPUT_TLS" == "true" ]; then
    ARG_TLS="--tls"
else
    ARG_TLS=""
fi
if [ "$INPUT_NOTICE" == "true" ]; then
    ARG_NOTICE="--notice"
else
    ARG_NOTICE=""
fi
if [ "$INPUT_VERBOSE" == "true" ]; then
    ARG_VERBOSE="--verbose"
else
    ARG_VERBOSE=""
fi
if [ "$INPUT_ANSICOLOR" == "true" ]; then
    ARG_ANSICOLOR="--ansicolor"
else
    ARG_ANSICOLOR=""
fi
if [ "$INPUT_MIRCCOLOR" == "true" ]; then
    ARG_MIRCCOLOR="--mirccolor"
else
    ARG_MIRCCOLOR=""
fi

ARG_TIMEOUT=""
if [ -n "$INPUT_TIMEOUT" ]; then
    ARG_TIMEOUT="--timeout $INPUT_TIMEOUT"
fi

exec /notify_irc.py \
    --server "$INPUT_SERVER" \
    --only-repository "$INPUT_ONLY_REPOSITORY" \
    --github-token "$INPUT_GITHUB_TOKEN" \
    --password "$INPUT_PASSWORD" \
    --port "$INPUT_PORT" \
    --nickname "$INPUT_NICKNAME" \
    --sasl-password "$INPUT_SASL_PASSWORD" \
    --channel "$INPUT_CHANNEL" \
    --channel-key "$INPUT_CHANNEL_KEY" \
    --eventpath "$INPUT_EVENTPATH" \
    $ARG_TIMEOUT $ARG_TLS $ARG_NOTICE $ARG_VERBOSE $ARG_ANSICOLOR $ARG_MIRCCOLOR

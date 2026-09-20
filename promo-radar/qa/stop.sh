#!/bin/bash
for p in $(ps aux | grep "[a]pp.cli serve" | awk '{print $2}'); do kill $p; done; sleep 1

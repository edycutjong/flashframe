#!/bin/bash
while kill -0 $(cat /Users/edycu/.gemini/antigravity-cli/brain/0ba65fbc-7ff0-4e0d-85d4-a19069ba55bd/.system_generated/tasks/task-55.pid) 2>/dev/null; do
  sleep 2
done
python3 control_bench.py > control_bench.log 2>&1
echo "Control bench done"

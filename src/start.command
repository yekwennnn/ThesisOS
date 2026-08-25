#!/bin/bash
# ThesisOS · 双击即可启动
cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1; then
  echo "没有找到 Node.js。"
  echo "请先到 https://nodejs.org 下载安装 Node.js（LTS 版本），然后重新双击本文件。"
  read -r -p "按回车退出…"
  exit 1
fi

exec node server.js --open

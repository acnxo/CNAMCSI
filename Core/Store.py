#!/usr/bin/env python3
"""结果存储：本地目录 或 S3（boto3 由 Lambda 运行时自带，无需打包）。

key 约定：
  latest/components.json          最新一期成分（覆盖写，用于算增减）
  daily/<date>/components.json    每日快照（归档）
  daily/<date>/components.csv
  state/stock_list.json           代码清单缓存（含上市日期，腾讯模式用）
"""

import json
import os


class LocalStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        p = os.path.join(self.root, *key.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def readJson(self, key):
        p = self._path(key)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def writeJson(self, key, obj):
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    def writeText(self, key, text):
        with open(self._path(key), "w", encoding="utf-8") as f:
            f.write(text)

    def describe(self):
        return f"local:{self.root}"


class S3Store:
    def __init__(self, bucket, prefix="microcap400"):
        import boto3  # 懒加载：本地跑时无需安装 boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.s3 = boto3.client("s3")

    def _key(self, key):
        return f"{self.prefix}/{key}" if self.prefix else key

    def readJson(self, key):
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(key))
            return json.loads(obj["Body"].read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def writeJson(self, key, obj):
        body = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=body,
                           ContentType="application/json; charset=utf-8")

    def writeText(self, key, text):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key),
                           Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8")

    def describe(self):
        return f"s3://{self.bucket}/{self.prefix}"


def makeStore(event, base_dir):
    bucket = event.get("s3_bucket") or os.environ.get("S3_BUCKET")
    if bucket:
        return S3Store(bucket, event.get("s3_prefix") or os.environ.get("S3_PREFIX", "microcap400"))
    return LocalStore(event.get("local_dir") or os.environ.get("LOCAL_DIR")
                      or os.path.join(base_dir, "output", "cloudfn"))

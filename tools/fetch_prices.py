# -*- coding: utf-8 -*-
"""抓 warframe.market v2 的价格快照，生成 sites/data/prices.js

为什么要本地预抓：浏览器的跨域请求被 warframe.market 拒绝（实测 Failed to fetch），
只能由本机/服务器端抓取后写成静态文件给网页读。
v1 接口已废弃（404/403），v2 可用：GET /v2/orders/item/{slug}/top
"""
import concurrent.futures
import json
import os
import threading
import time
import urllib.error
import urllib.request

PROJ = __import__('os').getcwd()
TARGETS = os.path.join(PROJ, 'data', 'raw', 'price_targets.json')
OUT = os.path.join(PROJ, 'sites', 'data', 'prices.js')
OUT_ORDERS = os.path.join(PROJ, 'sites', 'data', 'orders.js')
API = 'https://api.warframe.market/v2/orders/item/%s/top'
HEAD = {
    'User-Agent': 'WarframeCNLookup/1.0 (static fansite)',
    'Accept': 'application/json',
    'Language': 'zh-hans',
    'Platform': 'pc',
}
SLEEP = 0.35          # 约 3 请求/秒，对官方接口友好
LIMIT = int(os.environ.get('PRICE_LIMIT', '0'))   # 调试用：只抓前 N 个
TOP_N = int(os.environ.get('ORDERS_TOP', '5'))    # 每个物品保留前几个卖家/买家
ORDERS = os.environ.get('ORDERS', '1') != '0'     # 是否同时生成交易助手用的订单明细


def brief(o):
    """把一条订单压成紧凑格式：[卖家ID, 信誉, 数量, 单价, 状态]"""
    u = o.get('user') or {}
    return [u.get('ingameName') or '', int(u.get('reputation') or 0),
            int(o.get('quantity') or 1), int(o.get('platinum') or 0),
            (u.get('status') or '')[:3]]


def fetch(slug):
    req = urllib.request.Request(API % slug, headers=HEAD)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def main():
    tg = json.load(open(TARGETS, encoding='utf-8'))
    todo = tg['prime'] + tg['arcanes'] + tg['mods']
    if LIMIT:
        todo = todo[:LIMIT]
    out, orders, fails, done = {}, {}, 0, 0
    t0 = time.time()
    lock = threading.Lock()

    def work(it):
        """抓单个物品的 top 订单，返回 (slug, 价格结果或 None, 订单明细或 None)"""
        slug = it['slug']
        for attempt in range(3):
            try:
                d = (fetch(slug) or {}).get('data') or {}
                sell = [o for o in (d.get('sell') or []) if o.get('visible', True)]
                buy = [o for o in (d.get('buy') or []) if o.get('visible', True)]
                if not sell and not buy:
                    return slug, None, None
                low = min((o['platinum'] for o in sell), default=None)
                high = max((o['platinum'] for o in buy), default=None)
                online = sum(1 for o in sell + buy
                             if (o.get('user') or {}).get('status') in ('ingame', 'online'))
                book = None
                if ORDERS:
                    cheapest = sorted(sell, key=lambda o: o['platinum'])[:TOP_N]
                    dearest = sorted(buy, key=lambda o: -o['platinum'])[:TOP_N]
                    book = {'S': [brief(o) for o in cheapest], 'B': [brief(o) for o in dearest]}
                return slug, {'s': low, 'b': high, 'n': online, 'c': len(sell) + len(buy)}, book
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(5 + attempt * 5)
                    continue
                return slug, None, None
            except Exception:
                time.sleep(1 + attempt)
        return slug, None, None

    # 3 线程并发：约 3 请求/秒，对官方接口保持友好
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(work, it): it for it in todo}
        for fut in concurrent.futures.as_completed(futs):
            slug, res, book = fut.result()
            done += 1
            if res:
                out[slug] = res
                if book:
                    orders[slug] = book
            else:
                fails += 1
            if done % 75 == 0:
                print('  进度 %d/%d  已得 %d 条  失败 %d  用时 %.0fs'
                      % (done, len(todo), len(out), fails, time.time() - t0), flush=True)

    stamp = time.strftime('%Y-%m-%d %H:%M')
    js = ('/* warframe.market 价格快照（本地预抓，浏览器无法直连该接口）\n'
          '   抓取时间：%s · 覆盖 %d 个物品 · 数据版权归 warframe.market 及卖家所有 */\n'
          'window.WF_PRICES_STAMP=%s;\n'
          'window.WF_PRICES=%s;\n'
          % (stamp, len(out), json.dumps(stamp), json.dumps(out, ensure_ascii=False, separators=(',', ':'))))
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(js)

    if ORDERS:
        # 交易助手：每个物品的前 N 个卖家(S)/买家(B)，格式 [卖家ID, 信誉, 数量, 单价, 状态]
        ojs = ('/* warframe.market 挂单明细（交易助手用，按需加载）\n'
               '   抓取时间：%s · 覆盖 %d 个物品 · 每项最多 %d 个卖家/买家 */\n'
               'window.WF_ORDERS_STAMP=%s;\n'
               'window.WF_ORDERS=%s;\n'
               % (stamp, len(orders), TOP_N, json.dumps(stamp),
                  json.dumps(orders, ensure_ascii=False, separators=(',', ':'))))
        with open(OUT_ORDERS, 'w', encoding='utf-8') as f:
            f.write(ojs)
        print('挂单明细：%d 个物品 · %.0f KB' % (len(orders), len(ojs.encode()) / 1024))

    print('完成：%d 个物品有价格（失败 %d）· 用时 %.0fs · 文件 %.1f KB'
          % (len(out), fails, time.time() - t0, len(js.encode()) / 1024))
    for slug in list(out)[:5]:
        d = out[slug]
        print('   %-28s 最低卖 %-6s 最高收 %-6s 在线 %s' % (slug, d['s'], d['b'], d['n']))


if __name__ == '__main__':
    main()

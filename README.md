# 科技八卦局 · Tech Gossip

> 不止看科技,更要看科技圈的人和事。

每天一期 10–20 分钟的娱乐科技播客。主持:机器人 **瓜仔** —— 科技圈首席吃瓜官。
栏目:本周科技瓜 / 大佬那些事 / 大厂宫斗 / AI 圈秘闻 / 吃瓜深扒。

## 流水线

```
pipeline/fetch_news.py   # 采集: 12 个源(正规媒体 + HN/Reddit/V2EX 社区), 24h 窗口,
                         # 去重(data/seen.json), 八卦价值打分 → episodes/<日期>/news.json
(人工/agent)             # 选题 3–5 条 + 交叉核实 → 写瓜仔口播稿
                         # → episodes/<日期>/script.txt + summary.md(来源引注)
pipeline/tts_episode.py  # edge-tts 单主播(瓜仔) 合成 → episodes/<日期>/episode.mp3
pipeline/publish.py      # 音频写 Mac Studio 硬盘 → 重建 feed.xml/index.html
                         # → 推 GitHub Pages → 校验线上
```

## 对外地址

| 内容 | 地址 |
| --- | --- |
| RSS feed | https://lehaoda.github.io/tech-gossip/feed.xml |
| 节目页 | https://lehaoda.github.io/tech-gossip/ |
| 音频(硬盘 + nginx + Cloudflare) | https://podcast.lehaoda.com/tech_gossip/episodes/ |
| 封面(硬盘母版) | /Volumes/DataVault/podcast/tech_gossip/cover/cover.png |

## 内容规矩

- 事实、传闻、观点分开: 社区爆料不当事实, 必须至少一个正规媒体源交叉核实; 未证实的明确说"传闻"。
- 只研究和评论新闻, 不照读付费报道, 不搬运别的播客。
- summary.md 每条:`标题 | 来源 | 链接` —— feed 里渲染成来源链接。
- 音频零阿里云: 硬盘 + docker nginx + cloudflared。

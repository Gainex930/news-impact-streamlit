# 资讯影响监控台 Streamlit 版

这是用于部署到 Streamlit Community Cloud 的版本，功能包括：

- 国内外多源资讯刷新
- 财联社电报、韭研公社、东方财富、新浪财经、证券时报等国内源
- 顶部实时显示全A涨跌家数、领涨/领跌板块前三
- 资讯影响度、方向、板块、可信度自动分析
- 小作文/传闻手动录入
- 15 秒、30 秒、60 秒自动刷新

## 本地运行

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 部署到 Streamlit Community Cloud

1. 把本目录上传到一个 GitHub 仓库。
2. 打开 Streamlit Community Cloud 工作区。
3. 点击 `Create app`。
4. 选择 GitHub 仓库、分支和主文件：

```text
streamlit_app.py
```

5. 点击 `Deploy`。

如果想设置访问码，在 Streamlit app 的 Secrets 里添加：

```toml
ACCESS_CODE = "你的访问码"
```

不设置 `ACCESS_CODE` 时，页面默认公开访问。

## 注意

Streamlit Cloud 是境外运行环境，个别国内站点可能偶尔访问慢或被限制。程序会按来源独立失败，不会因为一个源失败导致整页不可用。

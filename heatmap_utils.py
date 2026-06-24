import pandas as pd
import plotly.graph_objects as go
import numpy as np
from collections import defaultdict

def generate_heatmap_html(excel_path):
    try:
        # 读取前三列（A日期，B时刻，C功率）
        df = pd.read_excel(excel_path, header=None, usecols='A:C', names=['日期', '时刻', '功率'])
        
        # 时刻标准化
        def parse_time(t):
            s = str(t).strip()
            if ":" in s:
                parts = s.split(":")
                if len(parts) >= 2:
                    h = parts[0].zfill(2)
                    m = parts[1].zfill(2)[:2]
                    return f"{h}:{m}"
            if s.isdigit() and len(s) <= 4:
                s = s.zfill(4)
                return f"{s[:2]}:{s[2:]}"
            return "00:00"
        df["时刻"] = df["时刻"].apply(parse_time)
        df["日期"] = pd.to_datetime(df["日期"], errors='coerce')
        df.dropna(subset=["日期", "功率"], inplace=True)
        if df.empty:
            return "<p>数据为空</p>"

        # 补全完整15分钟时刻
        all_times = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        def complete_times(group):
            group = group.set_index("时刻").reindex(all_times).reset_index()
            group["日期"] = group["日期"].ffill()
            return group
        df = df.groupby("日期", group_keys=False).apply(complete_times)

        # 透视
        pivot_df = df.pivot(index="时刻", columns="日期", values="功率")
        pivot_df = pivot_df.sort_index()
        x_dates = pivot_df.columns
        y_times = pivot_df.index
        z = pivot_df.values

        # 色标
        power_min = df["功率"].min()
        power_max = df["功率"].max()
        colorbar_ticks = np.linspace(power_min, power_max, 10).round(1)

        # X轴智能标签
        date_min = df["日期"].min()
        date_max = df["日期"].max()
        day_diff = (date_max - date_min).days
        xticks_pos = []
        xtick_labels = []
        SHOW_THRESHOLD = 10
        if day_diff <= 31:
            for idx, date in enumerate(x_dates):
                if idx % 3 == 0:
                    xticks_pos.append(idx)
                    xtick_labels.append(date.strftime("%m-%d"))
        else:
            month_data = defaultdict(list)
            for idx, date in enumerate(x_dates):
                month_key = date.strftime("%Y年%m月")
                month_data[month_key].append(idx)
            for month, idx_list in month_data.items():
                if len(idx_list) >= SHOW_THRESHOLD:
                    xticks_pos.append(idx_list[len(idx_list)//2])
                    xtick_labels.append(month)

        # 悬停数据
        date_strs = [d.strftime("%Y-%m-%d") for d in x_dates]
        customdata = np.tile(date_strs, (len(y_times), 1))

        # 绘图
        fig = go.Figure(data=go.Heatmap(
            z=z,
            x=list(range(len(x_dates))),
            y=y_times,
            colorscale="jet",
            zmin=power_min,
            zmax=power_max,
            hovertemplate="日期: %{customdata}<br>时间: %{y}<br>功率: %{z:.2f}<extra></extra>",
            customdata=customdata,
            colorbar=dict(title="功率", tickvals=colorbar_ticks, ticktext=[str(t) for t in colorbar_ticks])
        ))

        # ========== 关键修改：Y轴整点刻度（每4个点一个整点） ==========
        # 因为时刻是15分钟间隔，整点出现在索引 0,4,8,...,92（共24个）
        y_indices = list(range(0, len(y_times), 4))
        y_labels = [y_times[i] for i in y_indices]
        fig.update_yaxes(
            tickvals=y_indices,
            ticktext=y_labels,
            title_text="时间",
            autorange="reversed",
            tickfont=dict(size=11)
        )

        # X轴设置
        fig.update_xaxes(
            tickvals=xticks_pos,
            ticktext=xtick_labels,
            tickangle=-45,
            tickfont=dict(size=11),
            title_text="日期"
        )
        fig.update_layout(margin=dict(b=150, r=120), height=850)
        return fig.to_html(full_html=False, include_plotlyjs='cdn')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"<p>热力图生成失败：{str(e)}</p>"
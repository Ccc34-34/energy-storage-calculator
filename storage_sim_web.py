import pandas as pd
import numpy as np
import os
import re
from datetime import datetime

def run_simulation(excel_path, output_path=None, params_override=None, demand_override=None):
    # ================== 1. 读取数据 ==================
    df_raw = pd.read_excel(excel_path, sheet_name=0, header=None)

    # 负荷数据
    load_data = []
    for idx, row in df_raw.iterrows():
        date_val = row[0]
        if pd.notna(date_val):
            try:
                pd.to_datetime(date_val)
                load_data.append([row[0], row[1], row[2]])
            except:
                continue
    df_load = pd.DataFrame(load_data, columns=['日期', '时刻', '功率'])
    df_load['功率'] = pd.to_numeric(df_load['功率'], errors='coerce')
    df_load.dropna(subset=['功率'], inplace=True)
    df_load['datetime'] = pd.to_datetime(df_load['日期'].astype(str) + ' ' + df_load['时刻'].astype(str))
    df_load = df_load.sort_values('datetime').reset_index(drop=True)

    # 需量数据
    demand_dict = {}
    if demand_override is None:
        for idx, row in df_raw.iterrows():
            month_val = row[4]
            demand_val = row[5]
            if pd.notna(month_val) and pd.notna(demand_val):
                if isinstance(month_val, (int, float)):
                    month_str = str(int(month_val))
                else:
                    month_str = str(month_val).strip()
                    match = re.search(r'(\d{2,4})年(\d{1,2})月', month_str)
                    if match:
                        year = match.group(1)
                        month = match.group(2).zfill(2)
                        if len(year) == 2:
                            year = '20' + year
                        month_str = year + month
                if len(month_str) == 6 and month_str.isdigit():
                    demand_dict[month_str] = float(demand_val)
    else:
        demand_dict = demand_override

    # 参数表
    params = {}
    for idx, row in df_raw.iterrows():
        param_name = row[7]
        param_val = row[8]
        if pd.notna(param_name) and pd.notna(param_val):
            params[str(param_name).strip()] = param_val
    if params_override:
        for k, v in params_override.items():
            params[k] = v

    # ================== 2. 解析参数 ==================
    province = str(params.get('省份选择', '安徽'))
    P_nom = float(params.get('储能功率(kW)', 125))
    E_nom = float(params.get('储能容量(kWh)', 261))
    eta = float(params.get('充放电效率(%)', 100)) / 100.0
    SOC_min = float(params.get('SOC下限(%)', 0)) / 100.0
    SOC_max = float(params.get('SOC上限(%)', 100)) / 100.0
    SOC_init = float(params.get('初始SOC(%)', 30)) / 100.0
    limit_type = str(params.get('限制方式', '容量')).strip()
    S_tr = float(params.get('变压器容量(kVA)', 800))
    float_coef = float(params.get('浮动系数', 0.9))
    low_th = float(params.get('低功率阈值(kW)', 30))
    min_ch_power = float(params.get('最小充电功率阈值(kW)', 0))
    annual_decay = float(params.get('年衰减率(%)', 0)) / 100.0
    capacity_limit = S_tr * float_coef

    if limit_type == '需量' and not demand_dict:
        raise ValueError("需量模式下无有效需量数据（请手动添加或检查Excel E/F列）")

    # ================== 3. 预处理负荷数据 ==================
    df_load['分钟数'] = df_load['datetime'].dt.hour * 60 + df_load['datetime'].dt.minute
    df_load['月份'] = df_load['datetime'].dt.month
    df_load['年月'] = df_load['datetime'].dt.strftime('%Y%m')

    # 季节定义
    def get_season(province, month):
        if province == '安徽':
            if month in [2,3,4,5,6,10,11]:
                return 'spring_autumn'
            elif month in [7,8,9]:
                return 'summer'
            else:
                return 'winter'
        elif province == '江苏':
            if month in [3,4,5,9,10,11]:
                return 'spring_autumn'
            elif month in [6,7,8]:
                return 'summer'
            else:
                return 'winter'
        elif province == '浙江':
            if month in [2,3,4,5,6,9,10,11]:
                return 'spring_autumn'
            else:
                return 'summer_winter'  # 浙江夏冬季时段相同
        elif province == '广东':
            return 'all_year'
        elif province == '上海':
            if month in [1,2,3,4,5,6,9,10,11]:
                return 'spring_autumn'
            else:
                return 'summer'
        else:
            return 'spring_autumn'

    # 充放电时段判断
    def get_slots(province, season, minute):
        if province == '安徽':
            if season == 'spring_autumn':
                ch = (23*60 <= minute < 24*60) or (0 <= minute < 6*60) or (11*60 <= minute < 14*60)
                dis = (6*60 <= minute < 8*60) or (16*60 <= minute < 22*60)
            elif season == 'summer':
                ch = (2*60 <= minute < 9*60) or (11*60 <= minute < 13*60)
                dis = (9*60 <= minute < 11*60) or (16*60 <= minute < 24*60)
            else:  # winter
                ch = (23*60 <= minute < 24*60) or (0 <= minute < 6*60) or (12*60 <= minute < 14*60)
                dis = (6*60 <= minute < 12*60) or (15*60 <= minute < 23*60)
        elif province == '江苏':
            if season == 'spring_autumn':
                ch = (2*60 <= minute < 6*60) or (10*60 <= minute < 14*60)
                dis = (6*60 <= minute < 10*60) or (15*60 <= minute < 22*60)
            else:  # summer and winter (时段相同)
                ch = (0 <= minute < 6*60) or (11*60 <= minute < 13*60)
                dis = (6*60 <= minute < 11*60) or (14*60 <= minute < 22*60)
        elif province == '浙江':
            if season == 'spring_autumn':
                ch = (0 <= minute < 8*60) or (11*60 <= minute < 13*60)
                dis = (8*60 <= minute < 11*60) or (13*60 <= minute < 17*60)
            else:  # summer_winter
                ch = (0 <= minute < 8*60) or (11*60 <= minute < 13*60)
                dis = (8*60 <= minute < 11*60) or (15*60 <= minute < 23*60)
        elif province == '广东':
            # 全年统一
            ch = (0 <= minute < 8*60) or (12*60 <= minute < 14*60)
            dis = (10*60 <= minute < 12*60) or (14*60 <= minute < 19*60)
        elif province == '上海':
            if season == 'spring_autumn':
                ch = (22*60 <= minute < 24*60) or (0 <= minute < 6*60) or (11*60 <= minute < 18*60)
                dis = (8*60 <= minute < 11*60) or (18*60 <= minute < 21*60)
            else:  # summer
                ch = (22*60 <= minute < 24*60) or (0 <= minute < 6*60) or (15*60 <= minute < 18*60)
                dis = (8*60 <= minute < 15*60) or (18*60 <= minute < 21*60)
        else:
            ch = dis = False
        return ch, dis

    df_load['季节'] = df_load['月份'].apply(lambda m: get_season(province, m))
    df_load['is_charge'], df_load['is_discharge'] = zip(*df_load.apply(lambda r: get_slots(province, r['季节'], r['分钟数']), axis=1))

    # ================== 4. 初始化储能 ==================
    E_actual = E_nom * (1 - annual_decay)
    E_rem = E_nom * SOC_init
    SOC = E_rem / E_actual
    Δt = 0.25
    results = []

    def get_p_limit(year_month):
        if limit_type == '容量':
            return capacity_limit
        else:
            return demand_dict.get(year_month, 0) * float_coef

    # ================== 5. 逐时刻模拟 ==================
    for idx, row in df_load.iterrows():
        L = row['功率']
        dt = row['datetime']
        year_month = row['年月']
        ch = row['is_charge']
        dis = row['is_discharge']

        SOC_start = SOC * 100
        P_ch = 0.0
        P_dis = 0.0
        action = ''
        limit_reason = ''

        current_P_limit = get_p_limit(year_month)

        if ch and not dis:
            p_max1 = P_nom
            p_max2 = max(0, current_P_limit - L)
            energy_max_charge = (SOC_max - SOC) * E_actual
            p_max3 = energy_max_charge / Δt / eta if eta > 0 else 0
            p_max = min(p_max1, p_max2, p_max3)
            if p_max < min_ch_power:
                P_ch = 0
                action = '待机'
                limit_reason = '功率过低'
            else:
                P_ch = p_max
                action = '充电'
                if p_max == p_max1:
                    limit_reason = '额定功率'
                elif p_max == p_max2:
                    limit_reason = '电网限制'
                else:
                    limit_reason = 'SOC上限'
            E_rem += P_ch * Δt * eta

        elif dis and not ch:
            p_max1 = P_nom
            if L > low_th:
                p_max2 = L
            else:
                p_max2 = 0
            energy_max_discharge = (SOC - SOC_min) * E_actual
            p_max3 = energy_max_discharge / Δt * eta
            p_max = min(p_max1, p_max2, p_max3)
            if p_max <= 0:
                P_dis = 0
                action = '待机'
                if L <= low_th:
                    limit_reason = '低负荷阈值'
                elif SOC <= SOC_min:
                    limit_reason = 'SOC下限'
                else:
                    limit_reason = '无放电能力'
            else:
                P_dis = p_max
                action = '放电'
                if p_max == p_max1:
                    limit_reason = '额定功率'
                elif p_max == p_max2:
                    limit_reason = '负荷限制'
                else:
                    limit_reason = 'SOC下限'
            E_rem -= P_dis * Δt / eta
        else:
            action = '待机'
            limit_reason = '非充放时段'

        SOC = E_rem / E_actual
        SOC = np.clip(SOC, SOC_min, SOC_max)
        E_rem = SOC * E_actual
        SOC_end = SOC * 100

        results.append({
            '时间': dt,
            '负荷(kW)': L,
            '动作': action,
            '充电功率(kW)': P_ch,
            '放电功率(kW)': P_dis,
            '限制因素': limit_reason,
            '充电能量(kWh)': P_ch * Δt,
            '放电能量(kWh)': P_dis * Δt,
            'SOC_start(%)': SOC_start,
            'SOC_end(%)': SOC_end,
            '剩余能量(kWh)': E_rem
        })

    df_result = pd.DataFrame(results)

    # ================== 6. 汇总统计 ==================
    total_charge = df_result['充电能量(kWh)'].sum()
    total_discharge = df_result['放电能量(kWh)'].sum()
    total_net = total_charge - total_discharge
    avg_soc = (df_result['SOC_end(%)'] * Δt).sum() / (len(df_result) * Δt)
    max_soc = df_result['SOC_end(%)'].max()
    min_soc = df_result['SOC_end(%)'].min()

    # 汇总字典包含所有参数
    summary_dict = {
        '省份': province,
        '储能功率(kW)': f"{P_nom:.0f}",
        '储能容量(kWh)': f"{E_nom:.0f}",
        '充放电效率(%)': f"{eta*100:.0f}",
        'SOC下限(%)': f"{SOC_min*100:.0f}",
        'SOC上限(%)': f"{SOC_max*100:.0f}",
        '初始SOC(%)': f"{SOC_init*100:.0f}",
        '限制方式': limit_type,
        '变压器容量(kVA)': f"{S_tr:.0f}",
        '浮动系数': f"{float_coef:.2f}",
        '低功率阈值(kW)': f"{low_th:.0f}",
        '最小充电功率阈值(kW)': f"{min_ch_power:.0f}",
        '年衰减率(%)': f"{annual_decay*100:.1f}",
        '总充电量(kWh)': f"{total_charge:.2f}",
        '总放电量(kWh)': f"{total_discharge:.2f}",
        '净充电量(kWh)': f"{total_net:.2f}",
        '平均SOC(%)': f"{avg_soc:.2f}",
        'SOC最大值(%)': f"{max_soc:.2f}",
        'SOC最小值(%)': f"{min_soc:.2f}",
        '模拟天数': df_load['datetime'].dt.date.nunique(),
        '总时刻数': len(df_result),
    }

    # 保存 Excel
    if output_path is None:
        output_dir = os.path.dirname(excel_path)
        output_filename = f'储能模拟结果_{os.path.basename(excel_path)}'
        output_path = os.path.join(output_dir, output_filename)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df_result.to_excel(writer, sheet_name='详细结果', index=False)
        summary_df = pd.DataFrame(list(summary_dict.items()), columns=['指标', '数值'])
        summary_df.to_excel(writer, sheet_name='汇总', index=False)

    return output_path, summary_dict
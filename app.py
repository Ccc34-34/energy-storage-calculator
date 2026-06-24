from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import json
import shutil
import sqlite3
from datetime import datetime
from werkzeug.utils import secure_filename
from storage_sim_web import run_simulation
from heatmap_utils import generate_heatmap_html
import pytz

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

BASE_DIR = '/home/Ccc34'
DATABASE = os.path.join(BASE_DIR, 'users.db')

app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(BASE_DIR, 'outputs')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['MAX_OUTPUT_FILES'] = 3

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)

tz = pytz.timezone('Asia/Shanghai')

# ---------- 热力图缓存 ----------
heatmap_cache = {}  # 键: (文件路径, 修改时间戳), 值: HTML字符串

# ---------- 数据库初始化 ----------
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  is_admin INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  date TEXT NOT NULL,
                  count INTEGER DEFAULT 0,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    if not c.execute("SELECT * FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
                  ('admin', generate_password_hash('cl184894'), 1))
    if not c.execute("SELECT * FROM users WHERE username='Ccc34'").fetchone():
        c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
                  ('Ccc34', generate_password_hash('cl184894'), 1))
    conn.commit()
    conn.close()

init_db()

# ---------- 用户模型 ----------
class User(UserMixin):
    def __init__(self, id, username, is_admin):
        self.id = id
        self.username = username
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(row[0], row[1], row[2])
    return None

def get_today_beijing():
    return datetime.now(tz).date().isoformat()

def check_usage_limit(user_id, is_admin):
    if is_admin:
        return True
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    today = get_today_beijing()
    row = c.execute("SELECT count FROM usage WHERE user_id=? AND date=?", (user_id, today)).fetchone()
    if row is None:
        c.execute("INSERT INTO usage (user_id, date, count) VALUES (?, ?, ?)", (user_id, today, 1))
        conn.commit()
        conn.close()
        return True
    else:
        count = row[0]
        if count >= 1:
            conn.close()
            return False
        else:
            c.execute("UPDATE usage SET count = count + 1 WHERE user_id=? AND date=?", (user_id, today))
            conn.commit()
            conn.close()
            return True

# ---------- 路由 ----------
@app.route('/')
def index():
    if current_user.is_authenticated:
        return render_template('index.html')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        user = c.execute("SELECT id, password, is_admin FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            login_user(User(user[0], username, user[2]))
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password, is_admin) VALUES (?, ?, ?)",
                      (username, generate_password_hash(password), 0))
            conn.commit()
            flash('注册成功，请登录')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('用户名已存在')
        finally:
            conn.close()
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if not check_usage_limit(current_user.id, current_user.is_admin):
        return jsonify({'error': '您今日的免费次数已用完，请明天再试'}), 403

    if 'file' not in request.files:
        return jsonify({'error': '没有文件部分'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '未选择文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': '只支持 .xlsx 或 .xls 文件'}), 400

    filename = secure_filename(file.filename)
    unique_id = str(uuid.uuid4())
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_id}_{filename}")
    file.save(input_path)

    # 获取文件修改时间（用于缓存）
    file_mtime = os.path.getmtime(input_path)
    cache_key = (input_path, file_mtime)

    # 复选框状态
    run_simulation_flag = request.form.get('run_simulation') == 'on'
    generate_heatmap_flag = request.form.get('generate_heatmap') == 'on'

    # 覆盖参数（仅用于储能测算）
    params_override = {}
    if request.form.get('province'):
        params_override['省份选择'] = request.form['province']
    if request.form.get('p_nom'):
        params_override['储能功率(kW)'] = float(request.form['p_nom'])
    if request.form.get('e_nom'):
        params_override['储能容量(kWh)'] = float(request.form['e_nom'])
    if request.form.get('eta'):
        params_override['充放电效率(%)'] = float(request.form['eta'])
    if request.form.get('annual_decay'):
        params_override['年衰减率(%)'] = float(request.form['annual_decay'])
    if request.form.get('soc_min'):
        params_override['SOC下限(%)'] = float(request.form['soc_min'])
    if request.form.get('soc_max'):
        params_override['SOC上限(%)'] = float(request.form['soc_max'])
    if request.form.get('soc_init'):
        params_override['初始SOC(%)'] = float(request.form['soc_init'])
    if request.form.get('limit_type'):
        params_override['限制方式'] = request.form['limit_type']
    if request.form.get('s_tr'):
        params_override['变压器容量(kVA)'] = float(request.form['s_tr'])
    if request.form.get('float_coef'):
        params_override['浮动系数'] = float(request.form['float_coef'])
    if request.form.get('low_th'):
        params_override['低功率阈值(kW)'] = float(request.form['low_th'])
    if request.form.get('min_ch_power'):
        params_override['最小充电功率阈值(kW)'] = float(request.form['min_ch_power'])

    # 需量数据
    demand_override = None
    demand_json = request.form.get('demand_json')
    if demand_json:
        try:
            demand_list = json.loads(demand_json)
            demand_override = {item['month']: float(item['demand']) for item in demand_list}
        except Exception as e:
            return jsonify({'error': f'需量数据格式错误：{str(e)}'}), 400

    response_data = {'success': True}

    # 储能测算（仅当勾选时执行）
    if run_simulation_flag:
        try:
            output_path, summary_dict = run_simulation(
                excel_path=input_path,
                output_path=None,
                params_override=params_override,
                demand_override=demand_override
            )
            final_path = os.path.join(app.config['OUTPUT_FOLDER'], os.path.basename(output_path))
            shutil.move(output_path, final_path)
            cleanup_old_outputs()
            response_data['summary'] = summary_dict
            response_data['download_url'] = f'/download/{os.path.basename(final_path)}'
        except Exception as e:
            import traceback
            traceback.print_exc()
            response_data['success'] = False
            response_data['error'] = f'储能测算失败：{str(e)}'
            if os.path.exists(input_path):
                os.remove(input_path)
            return jsonify(response_data), 500

    # 热力图（仅当勾选时执行，并使用缓存）
    if generate_heatmap_flag:
        try:
            # 检查缓存
            if cache_key in heatmap_cache:
                heatmap_html = heatmap_cache[cache_key]
            else:
                heatmap_html = generate_heatmap_html(input_path)
                heatmap_cache[cache_key] = heatmap_html
            response_data['heatmap_html'] = heatmap_html
        except Exception as e:
            response_data['heatmap_html'] = f"<p>热力图生成失败：{str(e)}</p>"

    # 清理上传的临时文件（注意：如果后续还需要使用文件，不应删除；这里为了缓存，保留文件？但用户不会再上传同名文件，可删除）
    # 我们改为不删除，因为缓存依赖于文件存在。但为了节省空间，可以定期清理。此处保留文件以供缓存使用。
    # 为了简单，不删除临时文件，但会在缓存中保留。如果需要清理，可以设置定时任务。暂不删除。
    # if os.path.exists(input_path):
    #     os.remove(input_path)

    return jsonify(response_data)

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    safe = os.path.basename(filename)
    path = os.path.join(app.config['OUTPUT_FOLDER'], safe)
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name='储能模拟结果.xlsx')
    return jsonify({'error': '文件不存在'}), 404

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xlsx', 'xls'}

def cleanup_old_outputs():
    files = [f for f in os.listdir(app.config['OUTPUT_FOLDER']) if f.endswith('.xlsx')]
    if len(files) <= app.config['MAX_OUTPUT_FILES']:
        return
    files.sort(key=lambda f: os.path.getmtime(os.path.join(app.config['OUTPUT_FOLDER'], f)), reverse=True)
    for old in files[app.config['MAX_OUTPUT_FILES']:]:
        os.remove(os.path.join(app.config['OUTPUT_FOLDER'], old))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
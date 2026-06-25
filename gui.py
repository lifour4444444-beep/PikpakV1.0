import sys
if sys.version_info < (3, 9):
    import tkinter as _tk
    _tk.Tk().withdraw()
    import tkinter.messagebox as _mb
    _mb.showerror('版本错误', f'需要 Python 3.9+\n当前版本: {sys.version}')
    sys.exit(1)

import json
import io
import os
import queue
import random
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from PIL import Image as PILImage, ImageTk

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    _CFG_DIR = os.path.dirname(sys.executable)
else:
    _CFG_DIR = SCRIPT_DIR
sys.path.insert(0, SCRIPT_DIR)

import main as _worker


class _GUILogger:
    def __init__(self, queue_ref):
        self._queue = queue_ref
        self._buf = ''
        self._lock = threading.Lock()

    def write(self, text):
        if not text:
            return
        with self._lock:
            self._buf += text
            while '\n' in self._buf:
                idx = self._buf.index('\n')
                line = self._buf[:idx + 1]
                self._buf = self._buf[idx + 1:]
                if line.strip():
                    self._queue.put(('log', line))

    def flush(self):
        with self._lock:
            if self._buf.strip():
                self._queue.put(('log', self._buf + '\n'))
                self._buf = ''


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PikPak 批量邀请注册')
        self.geometry('1200x750')
        self.minsize(1000, 600)
        self.configure(bg='#0d1117')
        self.protocol('WM_DELETE_WINDOW', self._on_close)

        self._running = False
        self._worker_thread = None
        self._stop_flag = threading.Event()
        self._msg_queue = queue.Queue()
        self._log_handler = _GUILogger(self._msg_queue)

        self._mail_domains = []
        self._success_count = 0
        self._fail_count = 0
        self._round_count = 0

        self._build_style()
        self._build_ui()
        self._check_environment()
        self._load_config()
        self._log_buf = []
        self._log_max_lines = 3000
        self._log_last_flush = time.time()
        self._poll_messages()

    def _build_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')

        bg = '#0d1117'
        fg = '#c9d1d9'
        panel = '#161b22'
        border = '#30363d'
        accent = '#58a6ff'

        style.configure('.', background=bg, foreground=fg, fieldbackground=panel,
                        borderwidth=1, relief='flat')
        style.configure('TFrame', background=bg)
        style.configure('TLabelframe', background=bg, foreground=fg, bordercolor=border,
                        relief='solid', borderwidth=1)
        style.configure('TLabelframe.Label', background=bg, foreground=accent,
                        font=('Segoe UI', 9, 'bold'))
        style.configure('TLabel', background=bg, foreground=fg, font=('Segoe UI', 9))
        style.configure('TButton', background=panel, foreground=fg, font=('Segoe UI', 9),
                        borderwidth=1, relief='solid', padding=(8, 4))
        style.map('TButton',
                  background=[('active', '#21262d'), ('disabled', '#161b22')],
                  foreground=[('disabled', '#484f58')])
        style.configure('TEntry', fieldbackground=panel, foreground=fg, insertcolor=fg,
                        font=('Consolas', 9), padding=(4, 3))
        style.configure('TSpinbox', fieldbackground=panel, foreground=fg, arrowcolor=fg,
                        font=('Consolas', 9))
        style.configure('Accent.TButton', background='#1f6feb', foreground='white')
        style.map('Accent.TButton',
                  background=[('active', '#388bfd'), ('disabled', '#161b22')],
                  foreground=[('disabled', '#484f58')])
        style.configure('Danger.TButton', background='#da3633', foreground='white')
        style.map('Danger.TButton',
                  background=[('active', '#f85149'), ('disabled', '#161b22')],
                  foreground=[('disabled', '#484f58')])
        style.configure('TCombobox', fieldbackground=panel, foreground=fg, arrowcolor=fg,
                        font=('Consolas', 9))

        self.option_add('*TCombobox*Listbox.background', panel)
        self.option_add('*TCombobox*Listbox.foreground', fg)
        self.option_add('*TCombobox*Listbox.selectBackground', '#1f6feb')
        self.option_add('*TCombobox*Listbox.selectForeground', 'white')
        self.option_add('*TCombobox*Listbox.font', ('Consolas', 9))

    def _build_ui(self):
        header = tk.Frame(self, bg='#161b22', height=40)
        header.pack(fill=tk.X, padx=8, pady=(8, 0))
        header.pack_propagate(False)

        tk.Label(header, text='PikPak 批量邀请注册', fg='#58a6ff', bg='#161b22',
                 font=('Segoe UI', 13, 'bold')).pack(side=tk.LEFT, padx=(12, 0), pady=6)

        self._lbl_counts = tk.Label(header, text='成功: 0   失败: 0   本轮: 0',
                                    fg='#8b949e', bg='#161b22', font=('Consolas', 10))
        self._lbl_counts.pack(side=tk.RIGHT, padx=(0, 12), pady=6)

        main = ttk.Frame(self)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(paned)
        paned.add(left_panel, weight=35)

        right_panel = ttk.Frame(paned)
        paned.add(right_panel, weight=65)

        self._build_left_panel(left_panel)
        self._build_right_panel(right_panel)

        bar = tk.Frame(self, bg='#161b22', height=30)
        bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        bar.pack_propagate(False)

        self._lbl_status = tk.Label(bar, text='● 就绪', fg='#8b949e', bg='#161b22',
                                    font=('Segoe UI', 9), anchor='w')
        self._lbl_status.pack(side=tk.LEFT, padx=(10, 0), pady=4)

        self._lbl_domain = tk.Label(bar, text='', fg='#8b949e', bg='#161b22',
                                    font=('Segoe UI', 9))
        self._lbl_domain.pack(side=tk.RIGHT, padx=(0, 10), pady=4)

    def _build_left_panel(self, parent):
        canvas = tk.Canvas(parent, bg='#0d1117', highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind('<Configure>',
                          lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=scroll_frame, anchor='nw', tags='inner')
        canvas.configure(yscrollcommand=scroll.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_mousewheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

        def _resize_inner(event):
            canvas.itemconfig('inner', width=event.width)
        canvas.bind('<Configure>', _resize_inner)

        frm = ttk.LabelFrame(scroll_frame, text='┃ 注册设置', padding=10)
        frm.pack(fill=tk.X, padx=4, pady=(4, 6))

        self._config_vars = {}
        rows = [
            ('并发数', 'workers', tk.IntVar(value=1), 1, 20),
            ('注册间隔(分钟)', 'delay', tk.IntVar(value=_worker.DELAY_MINUTES), 1, 1440),
            ('最大注册数', 'max', tk.IntVar(value=0), 0, 99999),
        ]
        for label, key, var, lo, hi in rows:
            row = ttk.Frame(frm)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
            ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=10).pack(
                side=tk.LEFT, padx=(4, 0))
            self._config_vars[key] = var

        frm2 = ttk.LabelFrame(scroll_frame, text='┃ 邮箱域名', padding=10)
        frm2.pack(fill=tk.X, padx=4, pady=(0, 6))

        row = ttk.Frame(frm2)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='选择域名', width=14).pack(side=tk.LEFT)
        self._var_domain = tk.StringVar(value='随机')
        self._cbo_domain = ttk.Combobox(row, textvariable=self._var_domain,
                                        state='readonly', values=['随机', '加载中...'],
                                        width=24)
        self._cbo_domain.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(row, text='刷新', width=5, command=self._refresh_domains).pack(
            side=tk.LEFT, padx=4)

        frm3 = ttk.LabelFrame(scroll_frame, text='┃ 模型路径', padding=10)
        frm3.pack(fill=tk.X, padx=4, pady=(0, 6))

        self._var_yolo = tk.StringVar(value=_worker.YOLO_MODEL_PATH)
        self._var_siamese = tk.StringVar(value=_worker.SIAMESE_MODEL_PATH)
        self._var_v8 = tk.StringVar(value=_worker.V8_SUBMIT_JS)

        for label, var, filtr in [
            ('YOLOv5', self._var_yolo, ('ONNX', '*.onnx')),
            ('Siamese', self._var_siamese, ('ONNX', '*.onnx')),
            ('v8_submit.js', self._var_v8, ('JS', '*.js')),
        ]:
            row = ttk.Frame(frm3)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
            ent = ttk.Entry(row, textvariable=var, font=('Consolas', 8))
            ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            ttk.Button(row, text='...', width=3,
                       command=lambda v=var, f=filtr: self._browse(v, *f)
                       ).pack(side=tk.LEFT, padx=2)

        frm4 = ttk.LabelFrame(scroll_frame, text='┃ 代理 (轮换IP)', padding=10)
        frm4.pack(fill=tk.X, padx=4, pady=(0, 6))

        self._var_gateway = tk.StringVar(value=_worker.PROXY_GATEWAY)
        row = ttk.Frame(frm4)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='代理网关', width=14).pack(side=tk.LEFT)
        self._ent_gateway = ttk.Entry(row, textvariable=self._var_gateway, font=('Consolas', 8))
        self._ent_gateway.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        self._gateway_placeholder = '如: socks5h://user:pass@host:port'
        self._setup_entry_placeholder(self._ent_gateway, self._gateway_placeholder)

        frm5 = ttk.LabelFrame(scroll_frame, text='┃ 邀请链接', padding=10)
        frm5.pack(fill=tk.X, padx=4, pady=(0, 6))

        self._var_invite_link = tk.StringVar()
        row = ttk.Frame(frm5)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='邀请链接', width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self._var_invite_link, font=('Consolas', 8)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        frm6 = ttk.LabelFrame(scroll_frame, text='┃ 输出', padding=10)
        frm6.pack(fill=tk.X, padx=4, pady=(0, 6))

        self._var_result = tk.StringVar(value=_worker.RESULT_FILE)
        row = ttk.Frame(frm6)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='结果文件', width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self._var_result, font=('Consolas', 8)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        ttk.Button(row, text='...', width=3,
                   command=lambda: self._browse(self._var_result, 'TXT', '*.txt', save=True)
                   ).pack(side=tk.LEFT, padx=2)

        btn_row = ttk.Frame(scroll_frame)
        btn_row.pack(fill=tk.X, padx=4, pady=(10, 4))

        self._btn_start = ttk.Button(btn_row, text='▶  开始注册', style='Accent.TButton',
                                     command=self._start)
        self._btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self._btn_stop = ttk.Button(btn_row, text='■  停止', style='Danger.TButton',
                                    command=self._stop, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        ttk.Button(btn_row, text='保存配置', command=self._save_config).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2)

    def _build_right_panel(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text='  运行日志  ')

        toolbar = ttk.Frame(log_tab)
        toolbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(toolbar, text='运行日志', font=('Segoe UI', 10, 'bold'),
                  foreground='#58a6ff').pack(side=tk.LEFT)

        self._var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text='自动滚动', variable=self._var_autoscroll,
                        style='TCheckbutton').pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(toolbar, text='清空', width=5, command=self._clear_log).pack(side=tk.RIGHT)

        self._var_verbose = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text='详细', variable=self._var_verbose,
                        style='TCheckbutton').pack(side=tk.RIGHT, padx=(4, 0))

        log_frame = tk.Frame(log_tab, bg='#0d1117')
        log_frame.pack(fill=tk.BOTH, expand=True)

        self._log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED,
                                 font=('Consolas', 10), bg='#0d1117', fg='#c9d1d9',
                                 insertbackground='white', relief='flat', borderwidth=0,
                                 padx=8, pady=6)
        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                               command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        captcha_overlay = tk.Frame(log_tab, bg='#161b22', bd=1, relief='solid')
        captcha_overlay.place(relx=1.0, rely=1.0, x=-28, y=-12, anchor='se')
        captcha_overlay.lift()
        self._captcha_frame = captcha_overlay

        lbl = tk.Label(captcha_overlay, text='验证码', fg='#8b949e', bg='#161b22',
                       font=('Segoe UI', 8))
        lbl.pack(pady=(2, 0))

        self._captcha_canvas = tk.Canvas(captcha_overlay, bg='#0d1117',
                                         highlightthickness=0, width=180, height=180)
        self._captcha_canvas.pack(padx=4, pady=(0, 4))
        self._captcha_photo = None

        self._log_text.tag_configure('success', foreground='#3fb950')
        self._log_text.tag_configure('error', foreground='#f85149')
        self._log_text.tag_configure('warn', foreground='#d2991d')
        self._log_text.tag_configure('info', foreground='#58a6ff')
        self._log_text.tag_configure('email', foreground='#a371f7',
                                     font=('Consolas', 10, 'bold'))
        self._log_text.tag_configure('time', foreground='#8b949e')
        self._log_text.tag_configure('code', foreground='#79c0ff')
        self._log_text.tag_configure('count', foreground='#7ee787')
        self._log_text.tag_configure('header', foreground='#f0883e',
                                     font=('Consolas', 10, 'bold'))

        acct_tab = ttk.Frame(notebook)
        notebook.add(acct_tab, text='  账号列表  ')

        acct_toolbar = ttk.Frame(acct_tab)
        acct_toolbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(acct_toolbar, text='注册账号', font=('Segoe UI', 10, 'bold'),
                  foreground='#58a6ff').pack(side=tk.LEFT)
        ttk.Button(acct_toolbar, text='导出', width=5,
                   command=self._export_accounts).pack(side=tk.RIGHT, padx=2)
        ttk.Button(acct_toolbar, text='清空', width=5,
                   command=self._clear_accounts).pack(side=tk.RIGHT, padx=2)

        tree_frame = tk.Frame(acct_tab, bg='#0d1117')
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ('email', 'password', 'token', 'user_id', 'reg_time', 'invite')
        self._acct_tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                       selectmode='browse')
        self._acct_tree.heading('email', text='邮箱', anchor='w')
        self._acct_tree.heading('password', text='密码', anchor='w')
        self._acct_tree.heading('token', text='Token', anchor='w')
        self._acct_tree.heading('user_id', text='用户ID', anchor='w')
        self._acct_tree.heading('reg_time', text='注册时间', anchor='w')
        self._acct_tree.heading('invite', text='邀请', anchor='center')

        self._acct_tree.column('email', width=180, minwidth=120)
        self._acct_tree.column('password', width=100, minwidth=80)
        self._acct_tree.column('token', width=150, minwidth=100)
        self._acct_tree.column('user_id', width=120, minwidth=80)
        self._acct_tree.column('reg_time', width=130, minwidth=100)
        self._acct_tree.column('invite', width=60, minwidth=50, anchor='center')

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                    command=self._acct_tree.yview)
        self._acct_tree.configure(yscrollcommand=tree_scroll.set)
        self._acct_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        style = ttk.Style()
        style.configure('Treeview', background='#0d1117', foreground='#c9d1d9',
                        fieldbackground='#0d1117', rowheight=26)
        style.configure('Treeview.Heading', background='#161b22', foreground='#8b949e',
                        font=('Segoe UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', '#1f6feb')],
                  foreground=[('selected', 'white')])

    def _setup_entry_placeholder(self, entry, placeholder):
        entry.bind('<FocusIn>', lambda e: self._on_entry_focus_in(entry, placeholder))
        entry.bind('<FocusOut>', lambda e: self._on_entry_focus_out(entry, placeholder))
        if not entry.get():
            self._show_entry_placeholder(entry, placeholder)

    def _on_entry_focus_in(self, entry, placeholder):
        if entry.get() == placeholder:
            entry.delete(0, tk.END)
            entry.configure(foreground='#c9d1d9')

    def _on_entry_focus_out(self, entry, placeholder):
        if not entry.get():
            self._show_entry_placeholder(entry, placeholder)

    def _show_entry_placeholder(self, entry, placeholder):
        entry.delete(0, tk.END)
        entry.insert(0, placeholder)
        entry.configure(foreground='#484f58')

    def _setup_text_placeholder(self, text_widget, placeholder):
        text_widget.bind('<FocusIn>', lambda e: self._on_text_focus_in(text_widget, placeholder))
        text_widget.bind('<FocusOut>', lambda e: self._on_text_focus_out(text_widget, placeholder))
        if not text_widget.get('1.0', tk.END).strip():
            self._show_text_placeholder(text_widget, placeholder)

    def _on_text_focus_in(self, text_widget, placeholder):
        if text_widget.get('1.0', 'end-1c') == placeholder:
            text_widget.delete('1.0', tk.END)
            text_widget.configure(fg='#c9d1d9')

    def _on_text_focus_out(self, text_widget, placeholder):
        if not text_widget.get('1.0', tk.END).strip():
            self._show_text_placeholder(text_widget, placeholder)

    def _show_text_placeholder(self, text_widget, placeholder):
        text_widget.delete('1.0', tk.END)
        text_widget.insert('1.0', placeholder)
        text_widget.configure(fg='#484f58')

    def _browse(self, var, title, pattern, save=False):
        if save:
            path = filedialog.asksaveasfilename(
                title=title, filetypes=[(title, pattern)], defaultextension='.txt')
        else:
            path = filedialog.askopenfilename(title=title,
                                              filetypes=[(title, pattern)])
        if path:
            var.set(path)

    def _refresh_domains(self):
        self._cbo_domain.configure(values=['随机', '加载中...'])
        self._cbo_domain.set('加载中...')
        self._append_log('[系统] 获取邮箱域名列表...\n', 'info')

        def _fetch():
            import lib.mail
            try:
                domains = lib.mail.get_available_domains()
                self._mail_domains = domains
                values = ['随机'] + domains
                self.after(0, lambda: self._cbo_domain.configure(values=values))
                self.after(0, lambda: self._cbo_domain.set('随机'))
                self.after(0, lambda: self._append_log(
                    f'[系统] 获取到 {len(domains)} 个域名\n',
                    'success'))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda: self._cbo_domain.configure(values=['随机']))
                self.after(0, lambda: self._cbo_domain.set('随机'))
                self.after(0, lambda: self._append_log(
                    f'[系统] 域名获取失败: {err_msg}\n', 'error'))

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_config(self, callback=None):
        _worker.DELAY_MINUTES = self._config_vars['delay'].get()
        _worker.YOLO_MODEL_PATH = self._var_yolo.get()
        _worker.SIAMESE_MODEL_PATH = self._var_siamese.get()
        _worker.V8_SUBMIT_JS = self._var_v8.get()
        _worker.RESULT_FILE = self._var_result.get()
        _worker.VERBOSE = self._var_verbose.get()

        gateway = self._var_gateway.get().strip()
        if gateway == self._gateway_placeholder:
            gateway = ''
        _worker.PROXY_GATEWAY = gateway
        _worker.configure_proxy(gateway=gateway)

        domain_choice = self._var_domain.get()
        import lib.mail
        if domain_choice and domain_choice != '随机':
            lib.mail._FORCE_DOMAIN = domain_choice
        else:
            lib.mail._FORCE_DOMAIN = None

        invite_link = self._var_invite_link.get().strip()
        if invite_link:
            def _parse_invite():
                try:
                    self._msg_queue.put(('ui', 'log', f'[配置] 解析邀请链接: {invite_link}\n', 'info'))
                    parsed = _worker.parse_invite_link(invite_link)
                    _worker.INVITE_SHARE_ID = parsed['share_id']
                    _worker.INVITE_PASS_CODE_TOKEN = parsed['pass_code_token']
                    _worker.INVITE_TRACE_FILE_IDS = parsed['trace_file_ids']
                    self._msg_queue.put(('ui', 'log',
                        f'[配置] share_id={parsed["share_id"]}, '
                        f'trace_id={parsed["trace_file_ids"]}\n', 'info'))
                    if parsed.get('warning'):
                        self._msg_queue.put(('ui', 'log', f'[配置] ⚠ {parsed["warning"]}\n', 'warn'))
                except Exception as e:
                    self._msg_queue.put(('ui', 'log', f'[配置] 邀请链接解析失败: {e}\n', 'error'))
                    self._msg_queue.put(('ui', 'invite_error', str(e)))
                if callback:
                    self.after(0, callback)

            threading.Thread(target=_parse_invite, daemon=True).start()
        else:
            if callback:
                self.after(0, callback)

        self._lbl_domain.configure(
            text=f'域名: {domain_choice}' if domain_choice != '随机' else '')

    def _save_config(self):
        self._apply_config()
        cfg = {
            'delay': self._config_vars['delay'].get(),
            'workers': self._config_vars['workers'].get(),
            'max_rounds': self._config_vars['max'].get(),
            'yolo_path': self._var_yolo.get(),
            'siamese_path': self._var_siamese.get(),
            'v8_js': self._var_v8.get(),
            'result_file': self._var_result.get(),
            'proxy_gateway': '' if self._var_gateway.get() == self._gateway_placeholder else self._var_gateway.get(),
            'domain': self._var_domain.get(),
            'invite_link': self._var_invite_link.get(),
            'invite_share_id': _worker.INVITE_SHARE_ID,
            'invite_pass_code_token': _worker.INVITE_PASS_CODE_TOKEN,
            'invite_trace_file_ids': _worker.INVITE_TRACE_FILE_IDS,
        }
        path = os.path.join(_CFG_DIR, 'gui_config.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        self._append_log('[系统] 配置已保存\n', 'info')

    def _load_config(self):
        path = os.path.join(_CFG_DIR, 'gui_config.json')
        if not os.path.exists(path):
            self.after(500, self._refresh_domains)
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            self._config_vars['delay'].set(cfg.get('delay', _worker.DELAY_MINUTES))
            self._config_vars['workers'].set(cfg.get('workers', 1))
            self._config_vars['max'].set(cfg.get('max_rounds', 0))
            self._var_yolo.set(cfg.get('yolo_path', _worker.YOLO_MODEL_PATH))
            self._var_siamese.set(cfg.get('siamese_path', _worker.SIAMESE_MODEL_PATH))
            self._var_v8.set(cfg.get('v8_js', _worker.V8_SUBMIT_JS))
            self._var_result.set(cfg.get('result_file', _worker.RESULT_FILE))
            self._var_gateway.set(cfg.get('proxy_gateway', ''))
            saved_domain = cfg.get('domain', '随机')
            self._var_domain.set(saved_domain)
            self._var_invite_link.set(cfg.get('invite_link', ''))
            _worker.INVITE_SHARE_ID = cfg.get('invite_share_id', _worker.INVITE_SHARE_ID)
            _worker.INVITE_PASS_CODE_TOKEN = cfg.get('invite_pass_code_token', _worker.INVITE_PASS_CODE_TOKEN)
            _worker.INVITE_TRACE_FILE_IDS = cfg.get('invite_trace_file_ids', _worker.INVITE_TRACE_FILE_IDS)
        except Exception:
            pass
        self.after(500, self._refresh_domains)

    def _clear_log(self):
        self._log_buf.clear()
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete('1.0', tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _add_account(self, acct):
        self._acct_tree.insert('', 'end', values=(
            acct.get('email', ''),
            acct.get('password', ''),
            acct.get('access_token', '')[:40] + '...' if acct.get('access_token') else '',
            acct.get('user_id', ''),
            acct.get('reg_time', ''),
            acct.get('invite', ''),
        ))
        self._acct_tree.yview_moveto(1)

    def _clear_accounts(self):
        for item in self._acct_tree.get_children():
            self._acct_tree.delete(item)

    def _export_accounts(self):
        path = filedialog.asksaveasfilename(
            title='导出账号', filetypes=[('CSV', '*.csv'), ('TXT', '*.txt')],
            defaultextension='.csv')
        if not path:
            return
        with open(path, 'w', encoding='utf-8-sig') as f:
            f.write('邮箱,密码,Token,用户ID,注册时间,邀请\n')
            for item in self._acct_tree.get_children():
                values = self._acct_tree.item(item)['values']
                f.write(','.join(str(v) for v in values) + '\n')
        self._append_log(f'[系统] 已导出 {len(self._acct_tree.get_children())} 个账号 → {path}\n', 'success')

    def _append_log(self, text, tag=''):
        self._log_buf.append((text, tag))

    def _flush_log(self):
        if not self._log_buf:
            return
        self._log_text.configure(state=tk.NORMAL)
        for text, tag in self._log_buf:
            self._log_text.insert(tk.END, text, tag)
        self._log_buf.clear()
        total_lines = int(self._log_text.index('end-1c').split('.')[0])
        if total_lines > self._log_max_lines:
            self._log_text.delete('1.0', f'{total_lines - self._log_max_lines}.0')
        if self._var_autoscroll.get():
            self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _start(self):
        if not os.path.exists(self._var_v8.get()):
            messagebox.showerror('错误', f'v8_submit.js 不存在:\n{self._var_v8.get()}')
            return
        if not os.path.exists(self._var_yolo.get()):
            messagebox.showerror('错误', f'YOLO模型不存在:\n{self._var_yolo.get()}')
            return
        if not os.path.exists(self._var_siamese.get()):
            messagebox.showerror('错误', f'Siamese模型不存在:\n{self._var_siamese.get()}')
            return
        if not self._var_invite_link.get().strip():
            messagebox.showerror('错误', '请填写邀请链接')
            return

        self._btn_start.configure(state=tk.DISABLED)
        self._lbl_status.configure(text='● 准备中...', fg='#d2991d')
        self._append_log('[系统] 正在应用配置...\n', 'info')

        def _after_config():
            self._running = True
            self._stop_flag.clear()
            _worker._stop_event.clear()
            self._round_count = 0

            _worker.set_captcha_callback(
                lambda data: self._msg_queue.put(('ui', 'captcha_image', data)))

            self._btn_stop.configure(state=tk.NORMAL)
            self._lbl_status.configure(text='● 运行中', fg='#3fb950')

            workers = self._config_vars['workers'].get()
            self._append_log('━' * 50 + '\n', 'header')
            self._append_log('  PikPak 批量邀请注册\n', 'header')
            self._append_log(f'  并发: {workers}  |  间隔: {_worker.DELAY_MINUTES}min', 'info')
            self._append_log(f'  |  上限: {self._config_vars["max"].get() or "无限"}', 'info')
            self._append_log(f'  |  域名: {self._var_domain.get()}\n', 'info')
            if _worker.PROXY_GATEWAY:
                self._append_log(f'  代理: 网关 ({_worker.PROXY_GATEWAY[:50]}...)\n', 'info')
            else:
                self._append_log('  代理: 直连\n', 'warn')
            self._append_log('━' * 50 + '\n\n', 'header')

            sys.stdout = self._log_handler
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

        self._apply_config(callback=_after_config)

    def _stop(self):
        self._stop_flag.set()
        _worker.request_stop()
        self._append_log('\n[系统] 正在停止...\n', 'warn')

    def _worker_loop(self):
        max_rounds = self._config_vars['max'].get()
        workers = self._config_vars['workers'].get()
        round_lock = threading.Lock()
        round_counter = [0]

        def _next_round():
            with round_lock:
                round_counter[0] += 1
                return round_counter[0]

        def _worker_thread_func(worker_id):
            first_round = True
            rate_limit_count = 0
            try:
                while not self._stop_flag.is_set():
                    if not first_round:
                        self._msg_queue.put(
                            ('ui', 'log', f'\n⏳ 等待 {_worker.DELAY_MINUTES} 分钟...\n', 'info'))
                        for _ in range(_worker.DELAY_MINUTES * 60):
                            if self._stop_flag.is_set():
                                return
                            time.sleep(1)
                    first_round = False

                    stagger_delay = (worker_id - 1) * random.uniform(5, 55) / max(1, workers - 1) if workers > 1 else 0
                    if stagger_delay > 0:
                        self._msg_queue.put(
                            ('ui', 'log', '[Worker-{}] ⏱ 错开启动，等待 {:.0f}秒\n'.format(worker_id, stagger_delay), 'info'))
                        for _ in range(int(stagger_delay)):
                            if self._stop_flag.is_set():
                                return
                            time.sleep(1)

                    round_num = _next_round()
                    if max_rounds > 0 and round_num > max_rounds:
                        break

                    self._msg_queue.put(
                        ('ui', 'log', f'\n{"─" * 40}\n', 'header'))
                    self._msg_queue.put(
                        ('ui', 'log', f'第 {round_num} 轮 [Worker-{worker_id}]\n', 'header'))

                    _worker.pin_proxy()

                    current_ip = '获取失败'
                    for retry in range(3):
                        current_ip = _worker.get_current_ip()
                        if current_ip != '获取失败':
                            break
                        if retry < 2:
                            time.sleep(3)
                    self._msg_queue.put(
                        ('ui', 'log', f'🌐 出口IP: {current_ip}\n', 'email'))
                    self._msg_queue.put(
                        ('ui', 'log', f'{"─" * 40}\n', 'header'))

                    if current_ip == '获取失败':
                        self._msg_queue.put(
                            ('ui', 'log', '[Worker-{}] IP获取失败，跳过本轮\n'.format(worker_id), 'warn'))
                        with round_lock:
                            self._fail_count += 1
                        _worker.unpin_proxy()
                        continue

                    try:
                        _worker.set_worker_id(worker_id)
                        acct = _worker.run_batch_round(round_num)
                        rate_limit_count = 0
                        if acct:
                            with round_lock:
                                self._success_count += 1
                            self._msg_queue.put(('ui', 'account', acct))
                        else:
                            with round_lock:
                                self._fail_count += 1
                    except _worker.RateLimitError as _rl_err:
                        rate_limit_count += 1

                        self._msg_queue.put(
                            ('ui', 'log', '  [Worker-{}] ⛔ 频率限制 [{}]，延迟重试，本轮重新开始\n'.format(worker_id, _rl_err.endpoint), 'warn'))

                        _worker.unpin_proxy()
                        _worker.force_rotate_proxy()
                        _worker.pin_proxy()

                        if rate_limit_count >= 3:
                            with round_lock:
                                self._fail_count += 1
                            self._msg_queue.put(
                                ('ui', 'log', '[Worker-{}] 频率限制重试3次无效，跳过本轮\n'.format(worker_id), 'warn'))
                            rate_limit_count = 0
                            first_round = False
                            continue
                        first_round = True
                        continue
                    except Exception as e:
                        with round_lock:
                            self._fail_count += 1
                        self._msg_queue.put(
                            ('ui', 'log', f'\n[系统] 运行出错: {e}\n', 'error'))
                        _worker.force_rotate_proxy()

                    with round_lock:
                        self._round_count = round_num
                    self._msg_queue.put(
                        ('ui', 'count', (self._success_count, self._fail_count,
                                         self._round_count)))

                    if max_rounds > 0 and self._round_count >= max_rounds:
                        break
            finally:
                _worker.unpin_proxy()

        threads = []
        for i in range(1, workers + 1):
            t = threading.Thread(target=_worker_thread_func, args=(i,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        sys.stdout = sys.__stdout__
        self._msg_queue.put(('ui', 'done', None))

    def _poll_messages(self):
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                if msg[0] == 'log':
                    self._append_log(msg[1])
                elif msg[0] == 'ui':
                    ui_type = msg[1]
                    if ui_type == 'log':
                        text = msg[2] if len(msg) > 2 else ''
                        tag = msg[3] if len(msg) > 3 else ''
                        self._append_log(text, tag)
                    elif ui_type == 'count':
                        s, f, r = msg[2]
                        self._lbl_counts.configure(
                            text=f'成功: {s}   失败: {f}   本轮: {r}',
                            fg='#3fb950' if s > 0 else '#8b949e')
                    elif ui_type == 'account':
                        acct = msg[2]
                        self._add_account(acct)
                    elif ui_type == 'captcha_image':
                        png_data = msg[2]
                        threading.Thread(target=self._show_captcha_bg,
                                         args=(png_data,), daemon=True).start()
                    elif ui_type == 'invite_error':
                        err = msg[2] if len(msg) > 2 else ''
                        messagebox.showwarning('邀请链接', f'解析失败: {err}\n\n将使用默认邀请参数')
                    elif ui_type == 'stop':
                        self._finish()
                    elif ui_type == 'done':
                        self._append_log('\n[系统] 任务结束\n', 'info')
                        self._finish()
        except queue.Empty:
            pass
        now = time.time()
        if self._log_buf and now - self._log_last_flush > 0.2:
            self._flush_log()
            self._log_last_flush = now
        self.after(100, self._poll_messages)

    def _finish(self):
        self._flush_log()
        self._running = False
        self._btn_start.configure(state=tk.NORMAL)
        self._btn_stop.configure(state=tk.DISABLED)
        self._lbl_status.configure(text='● 就绪', fg='#8b949e')
        self._lbl_counts.configure(
            text=f'成功: {self._success_count}   失败: {self._fail_count}   本轮: {self._round_count}',
            fg='#3fb950' if self._success_count > 0 else '#8b949e')
        self._captcha_photo = None
        self._captcha_canvas.delete('all')

    def _show_captcha_bg(self, png_data):
        try:
            img = PILImage.open(io.BytesIO(png_data))
            img.thumbnail((170, 170), PILImage.LANCZOS)
            self.after(0, lambda i=img: self._show_captcha_ui(i))
        except Exception:
            pass

    def _show_captcha_ui(self, img):
        try:
            self._captcha_photo = ImageTk.PhotoImage(img)
            self._captcha_canvas.delete('all')
            self._captcha_canvas.create_image(90, 90, image=self._captcha_photo, anchor='center')
        except Exception:
            pass

    def _check_environment(self):
        warnings = []

        try:
            result = subprocess.run(
                ['node', '--version'], capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            node_ver = result.stdout.strip()
            major = int(node_ver.lstrip('v').split('.')[0])
            if major < 12:
                warnings.append(f'Node.js 版本过低 ({node_ver})，需要 v12+')
        except FileNotFoundError:
            warnings.append('未安装 Node.js，请安装 https://nodejs.org/')
        except Exception:
            warnings.append('Node.js 检测失败，可能未正确安装')

        v8_js = _worker.V8_SUBMIT_JS
        if not os.path.exists(v8_js):
            warnings.append(f'v8_submit.js 不存在: {v8_js}')

        node_modules = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'node_modules')
        socks_agent = os.path.join(node_modules, 'socks-proxy-agent')
        if not os.path.exists(socks_agent):
            warnings.append('Node.js 依赖未安装，请运行: npm install')

        yolo_path = _worker.YOLO_MODEL_PATH
        siamese_path = _worker.SIAMESE_MODEL_PATH
        if not os.path.exists(yolo_path):
            warnings.append(f'YOLO模型不存在: {yolo_path}')
        if not os.path.exists(siamese_path):
            warnings.append(f'Siamese模型不存在: {siamese_path}')

        try:
            import onnxruntime
        except ImportError:
            warnings.append('onnxruntime 未安装，请运行: pip install onnxruntime')
        except Exception as e:
            if 'DLL' in str(e) or 'LoadLibrary' in str(e):
                warnings.append(f'onnxruntime 加载失败(缺少VC运行库): {e}')
            else:
                warnings.append(f'onnxruntime 加载失败: {e}')

        try:
            import socks
        except ImportError:
            warnings.append('PySocks 未安装，代理功能不可用。请运行: pip install PySocks')

        if warnings:
            self.after(100, lambda: self._show_env_warnings(warnings))

    def _show_env_warnings(self, warnings):
        msg = '检测到以下兼容性问题：\n\n'
        for i, w in enumerate(warnings, 1):
            msg += f'{i}. {w}\n'
        msg += '\n部分功能可能无法正常使用。'
        messagebox.showwarning('环境检查', msg)

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno('确认', '任务正在运行中，确定退出？'):
                return
            self._stop_flag.set()
        self.destroy()


if __name__ == '__main__':
    app = App()
    app.mainloop()
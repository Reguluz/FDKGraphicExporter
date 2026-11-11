// Native Windows front end. Conversion stays in the separately testable Python backend.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("FDK Asset Tool")]
[assembly: AssemblyProduct("FDKGraphicExporter")]
[assembly: AssemblyVersion("1.3.0.0")]
[assembly: AssemblyFileVersion("1.3.0.0")]

namespace FDK {
    internal static class Program {
        [STAThread]
        static int Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try {
                string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
                using (ExporterForm form = new ExporterForm(root)) {
                    if (args.Length == 2 && args[0] == "--self-test") return form.SelfTest(args[1]);
                    if (args.Length == 2 && args[0] == "--integration-test") return form.IntegrationTest(args[1]);
                    if (args.Length == 2 && args[0] == "--portable-test") return form.PortableTest(args[1]);
                    Application.Run(form);
                }
                return 0;
            } catch (Exception ex) {
                if (args.Contains("--self-test") || args.Contains("--integration-test") || args.Contains("--portable-test")) { Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(args.Last()))); File.WriteAllText(args.Last() + ".error.txt", ex.ToString()); return 1; }
                MessageBox.Show(ex.Message, "资源工具启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
    }

    internal sealed class ProfileRow {
        public Dictionary<string, object> Data;
        public string Id { get { return Convert.ToString(Data["id"]); } }
        public override string ToString() { return Convert.ToString(Data["name"]); }
    }

    internal sealed class ModelRow {
        public string Id, Family, Bones, Materials, Textures, State;
    }

    internal sealed class ExporterForm : Form {
        readonly string root;
        readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16777216 };
        readonly List<ModelRow> models = new List<ModelRow>();
        readonly HashSet<string> selected = new HashSet<string>(StringComparer.Ordinal);
        readonly List<Control> mutable = new List<Control>();
        readonly Color ink = Color.FromArgb(30, 44, 65), accent = Color.FromArgb(36, 91, 185);
        TextBox game, blender, search, log, outputPath;
        ComboBox profiles;
        ProfileRow currentProfile;
        readonly Dictionary<string, Dictionary<string, object>> profileStates = new Dictionary<string, Dictionary<string, object>>();
        string OutputRoot { get { return currentProfile == null ? root : ConfigPath(Value(currentProfile.Data, "output")); } }
        string PythonPath { get { return Path.GetFullPath(Path.Combine(root, "..", "tools", "python", "Scripts", "python.exe")); } }
        string BackendPath {
            get {
                string source = Path.Combine(root, "tool", "fdk_backend.py");
                return File.Exists(source) ? source : source + "c";
            }
        }
        ComboBox family;
        NumericUpDown workers;
        CheckBox rescan, rebuild;
        RadioButton allModels, selectedModels;
        ListView modelList;
        Label count, phase, location;
        ProgressBar progress;
        Button start, cancel, check, save, refresh, scan;
        bool populating, busy, closeWhenDone, checking, integrationTesting;
        Process process;
        NativeJob job;
        string cancelFile, taskLog, lastError;
        bool doneReceived;
        string doneStatus;
        int lastExitCode;
        ToolTip tips = new ToolTip { AutoPopDelay = 15000 };

        public ExporterForm(string folder) {
            root = folder;
            Text = "FDK 资源工具";
            Font = new Font("Microsoft YaHei UI", 9F);
            BackColor = Color.FromArgb(243, 246, 250);
            ForeColor = ink;
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoScaleDimensions = new SizeF(96, 96);
            MinimumSize = new Size(1060, 800);
            Size = new Size(1220, 920);
            StartPosition = FormStartPosition.CenterScreen;
            BuildUi();
            LoadConfig();
            FormClosing += OnClosing;
        }

        Button Button(string caption, EventHandler action, bool primary = false) {
            Button b = new Button { Text = caption, AutoSize = true, Height = 33,
                MinimumSize = new Size(90, 32), Padding = new Padding(9, 3, 9, 3),
                FlatStyle = FlatStyle.Flat, Margin = new Padding(0, 0, 8, 0),
                BackColor = primary ? accent : Color.White, ForeColor = primary ? Color.White : ink };
            b.FlatAppearance.BorderColor = primary ? accent : Color.FromArgb(205, 214, 226);
            b.Click += action;
            return b;
        }

        Label Label(string text) {
            return new Label { Text = text, AutoSize = true, Anchor = AnchorStyles.Left,
                Margin = new Padding(0, 4, 8, 4) };
        }

        void BuildUi() {
            TableLayoutPanel outer = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1,
                RowCount = 5, Padding = new Padding(20, 14, 20, 10) };
            outer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 64));
            outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 174));
            outer.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 232));
            outer.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
            Controls.Add(outer);

            Panel header = new Panel { Dock = DockStyle.Fill };
            header.Controls.Add(new Label { Text = "FDK 资源工具", Location = new Point(0, 0),
                AutoSize = true, Font = new Font(Font.FontFamily, 19, FontStyle.Bold), ForeColor = ink });
            header.Controls.Add(new Label { Text = "选择游戏 · 浏览模型与贴图 · 共享导出与 Blender 预览",
                Location = new Point(2, 38), AutoSize = true, ForeColor = Color.FromArgb(95, 111, 133) });
            Button help = Button("使用说明", delegate { ShowHelp(); });
            help.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            header.Controls.Add(help);
            header.Resize += delegate { help.Location = new Point(header.ClientSize.Width - help.Width, 10); };
            outer.Controls.Add(header, 0, 0);

            TableLayoutPanel paths = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Color.White,
                Padding = new Padding(12, 8, 8, 8), ColumnCount = 3, RowCount = 4, Margin = new Padding(0, 0, 0, 12) };
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
            for (int i = 0; i < 4; i++) paths.RowStyles.Add(new RowStyle(SizeType.Percent, 25));
            profiles = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill, Margin = new Padding(0, 3, 10, 0) };
            profiles.SelectedIndexChanged += delegate { if (!populating) ActivateProfile(); };
            paths.Controls.Add(Label("游戏配置"), 0, 0); paths.Controls.Add(profiles, 1, 0);
            paths.SetColumnSpan(profiles, 2);
            game = new TextBox { Dock = DockStyle.Fill, Margin = new Padding(0, 5, 10, 4) };
            blender = new TextBox { Dock = DockStyle.Fill, Margin = new Padding(0, 5, 10, 4) };
            paths.Controls.Add(Label("游戏目录"), 0, 1); paths.Controls.Add(game, 1, 1);
            Button browseGame = Button("浏览…", delegate { BrowseGame(); });
            paths.Controls.Add(browseGame, 2, 1);
            paths.Controls.Add(Label("Blender 程序"), 0, 2); paths.Controls.Add(blender, 1, 2);
            Button browseBlender = Button("浏览…", delegate { BrowseBlender(); });
            paths.Controls.Add(browseBlender, 2, 2);
            paths.Controls.Add(Label("资源输出目录"), 0, 3);
            outputPath = new TextBox { Text = "", ReadOnly = true, Dock = DockStyle.Fill,
                BorderStyle = BorderStyle.None, BackColor = Color.White, Margin = new Padding(0, 9, 10, 0) };
            paths.Controls.Add(outputPath, 1, 3);
            paths.Controls.Add(Button("打开目录", delegate { OpenPath(OutputRoot); }), 2, 3);
            outer.Controls.Add(paths, 0, 1);
            mutable.AddRange(new Control[] { profiles, game, blender, browseGame, browseBlender });

            TabControl tabs = new TabControl { Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 10) };
            TabPage exports = new TabPage("模型选择与导出") { BackColor = Color.White, Padding = new Padding(10) };
            TabPage addon = new TabPage("Blender 插件") { BackColor = Color.White, Padding = new Padding(18) };
            tabs.TabPages.Add(exports); tabs.TabPages.Add(addon);
            outer.Controls.Add(tabs, 0, 2);

            TableLayoutPanel columns = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1 };
            columns.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            columns.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 268));
            columns.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            exports.Controls.Add(columns);
            TableLayoutPanel listArea = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4,
                Margin = new Padding(0, 0, 16, 0) };
            listArea.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            listArea.RowStyles.Add(new RowStyle(SizeType.Absolute, 39));
            listArea.RowStyles.Add(new RowStyle(SizeType.Absolute, 37));
            listArea.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            listArea.RowStyles.Add(new RowStyle(SizeType.Absolute, 26));
            columns.Controls.Add(listArea, 0, 0);

            TableLayoutPanel filters = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 1, Margin = Padding.Empty };
            filters.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 42));
            filters.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            filters.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 160));
            filters.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 100));
            filters.Controls.Add(Label("搜索"), 0, 0);
            search = new TextBox { Dock = DockStyle.Fill, Margin = new Padding(0, 3, 12, 0) };
            tips.SetToolTip(search, "按资源 ID 搜索，例如 chr0000、chr5117、equ；支持空格分隔多个条件。");
            search.TextChanged += delegate { FilterCatalog(); }; filters.Controls.Add(search, 1, 0);
            family = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Dock = DockStyle.Fill, Margin = new Padding(0, 3, 10, 0) };
            family.SelectedIndexChanged += delegate { FilterCatalog(); }; filters.Controls.Add(family, 2, 0);
            refresh = Button("刷新", delegate { LoadCatalog(); }); filters.Controls.Add(refresh, 3, 0);
            listArea.Controls.Add(filters, 0, 0);

            FlowLayoutPanel selectionBar = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
            Button visible = Button("勾选筛选结果", delegate { SelectVisible(); });
            Button clear = Button("清空所有选择", delegate { selected.Clear(); FilterCatalog(); });
            scan = Button("扫描模型目录", delegate { BeginTask(false, "scan"); });
            tips.SetToolTip(scan, "只扫描当前游戏并建立模型列表，不导出 FBX。首次使用新游戏时先扫描。");
            selectionBar.Controls.Add(scan); selectionBar.Controls.Add(visible); selectionBar.Controls.Add(clear);
            listArea.Controls.Add(selectionBar, 0, 1);
            modelList = new ListView { View = View.Details, CheckBoxes = true, FullRowSelect = true,
                HideSelection = false, Dock = DockStyle.Fill, BorderStyle = BorderStyle.FixedSingle,
                GridLines = false, MultiSelect = true, Margin = Padding.Empty };
            modelList.Columns.Add("模型 ID", 200); modelList.Columns.Add("类别", 75);
            modelList.Columns.Add("骨骼", 65); modelList.Columns.Add("材质", 60);
            modelList.Columns.Add("贴图", 60); modelList.Columns.Add("文件状态", 110);
            modelList.ItemChecked += delegate(object sender, ItemCheckedEventArgs e) {
                if (populating) return;
                if (e.Item.Checked) selected.Add(e.Item.Text); else selected.Remove(e.Item.Text);
                selectedModels.Checked = true; UpdateCount();
            };
            modelList.KeyDown += delegate(object sender, KeyEventArgs e) {
                if (e.Control && e.KeyCode == Keys.A) { SelectVisible(); e.SuppressKeyPress = true; }
            };
            listArea.Controls.Add(modelList, 0, 2);
            count = Label(""); count.ForeColor = Color.FromArgb(95, 111, 133); listArea.Controls.Add(count, 0, 3);
            mutable.AddRange(new Control[] { search, family, refresh, scan, visible, clear, modelList });

            FlowLayoutPanel options = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
                WrapContents = false, AutoScroll = true, Margin = Padding.Empty, Padding = new Padding(5, 0, 0, 0) };
            columns.Controls.Add(options, 1, 0);
            Label optionsTitle = Label("导出选项"); optionsTitle.Font = new Font(Font, FontStyle.Bold); options.Controls.Add(optionsTitle);
            allModels = new RadioButton { Text = "全部模型", Checked = true, AutoSize = true, Margin = new Padding(0, 6, 0, 4) };
            selectedModels = new RadioButton { Text = "仅导出勾选的模型", AutoSize = true, Margin = new Padding(0, 2, 0, 10) };
            allModels.CheckedChanged += delegate { UpdateCount(); };
            selectedModels.CheckedChanged += delegate { UpdateCount(); };
            options.Controls.Add(allModels); options.Controls.Add(selectedModels);
            FlowLayoutPanel concurrency = new FlowLayoutPanel { Width = 240, Height = 34, Margin = Padding.Empty };
            concurrency.Controls.Add(Label("并行任务数"));
            workers = new NumericUpDown { Minimum = 1, Maximum = 32, Value = 8, Width = 72 };
            concurrency.Controls.Add(workers); options.Controls.Add(concurrency);
            rescan = new CheckBox { Text = "重新扫描游戏资源", AutoSize = true, Margin = new Padding(0, 4, 0, 4) };
            rebuild = new CheckBox { Text = "重建贴图和中间模型", AutoSize = true, Margin = new Padding(0, 4, 0, 4) };
            tips.SetToolTip(rescan, "游戏更新或更换游戏目录时使用；重新扫描会同时重建本次范围内的中间资源。");
            tips.SetToolTip(rebuild, "通常无需勾选；默认复用完整资源，并按输入变化决定是否重新导出 FBX。");
            options.Controls.Add(rescan); options.Controls.Add(rebuild);
            options.Controls.Add(new Label { Text = "自动续跑与导出校验始终开启。\n默认复用已完成的资源。", AutoSize = true,
                ForeColor = Color.FromArgb(95, 111, 133), Margin = new Padding(0, 8, 0, 12) });
            FlowLayoutPanel settingsButtons = new FlowLayoutPanel { Width = 248, Height = 38, Margin = Padding.Empty };
            check = Button("检查环境", delegate { BeginTask(true); });
            save = Button("保存设置", delegate { try { SaveSettings(); phase.Text = "设置已保存。"; } catch (Exception ex) { Error(ex.Message); } });
            settingsButtons.Controls.Add(check); settingsButtons.Controls.Add(save); options.Controls.Add(settingsButtons);
            mutable.AddRange(new Control[] { allModels, selectedModels, workers, rescan, rebuild, check, save });

            FlowLayoutPanel pluginArea = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
                WrapContents = false, AutoScroll = true };
            addon.Controls.Add(pluginArea);
            Label pluginTitle = Label("安装后，直接在 Blender 侧栏使用"); pluginTitle.Font = new Font(Font.FontFamily, 13, FontStyle.Bold);
            pluginArea.Controls.Add(pluginTitle);
            pluginArea.Controls.Add(new Label { AutoSize = true, MaximumSize = new Size(970, 0), Margin = new Padding(0, 12, 0, 14),
                Text = "1. 打开 Blender 4.5 或 5.2，进入 编辑 → 偏好设置 → 插件。\n\n" +
                "2. 从右上角菜单选择 从磁盘安装（Install from Disk），选择本目录的 FDK_Blender_Addon.zip。\n\n" +
                "3. 启用 FDK 模型与材质预览，在插件偏好中选择资源目录并保存偏好。\n\n" +
                "4. 回到 3D 视图，按 N → FDK，刷新列表、选择模型，再点击 导入模型并恢复材质。" });
            FlowLayoutPanel pluginButtons = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = Padding.Empty };
            pluginButtons.Controls.Add(Button("定位插件 ZIP", delegate { Reveal(Path.Combine(root, "FDK_Blender_Addon.zip")); }));
            pluginButtons.Controls.Add(Button("打开 Blender", delegate { OpenBlender(); }, true));
            pluginButtons.Controls.Add(Button("插件使用说明", delegate { ShowHelp("BLENDER_ADDON.md"); }));
            pluginArea.Controls.Add(pluginButtons);
            pluginArea.Controls.Add(new Label { AutoSize = true, ForeColor = Color.FromArgb(95, 111, 133), Margin = new Padding(0, 18, 0, 0),
                Text = "包含骨架与蒙皮；当前未导出动画。材质为预览近似，特殊着色输入保留为参考。" });

            TableLayoutPanel task = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4, Margin = Padding.Empty };
            task.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            task.RowStyles.Add(new RowStyle(SizeType.Absolute, 44));
            task.RowStyles.Add(new RowStyle(SizeType.Absolute, 27));
            task.RowStyles.Add(new RowStyle(SizeType.Absolute, 18));
            task.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            FlowLayoutPanel actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
            start = Button("开始导出", delegate { BeginTask(false); }, true); start.MinimumSize = new Size(160, 35);
            cancel = Button("停止任务", delegate { CancelTask(); }); cancel.Enabled = false;
            actions.Controls.Add(start); actions.Controls.Add(cancel);
            actions.Controls.Add(Button("查看导出文件", delegate { OpenPath(Path.Combine(OutputRoot, "fbx")); }));
            actions.Controls.Add(Button("查看日志", delegate { if (File.Exists(taskLog)) Reveal(taskLog); else OpenPath(Path.Combine(OutputRoot, "reports")); }));
            task.Controls.Add(actions, 0, 0);
            phase = Label("就绪 · 选择范围和选项后开始导出。"); phase.Dock = DockStyle.Fill; phase.AutoEllipsis = true;
            task.Controls.Add(phase, 0, 1);
            progress = new ProgressBar { Dock = DockStyle.Fill, Minimum = 0, Maximum = 100, Margin = new Padding(0, 0, 0, 6) };
            task.Controls.Add(progress, 0, 2);
            log = new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill, ScrollBars = ScrollBars.Vertical,
                BackColor = Color.FromArgb(248, 250, 253), ForeColor = ink, BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Microsoft YaHei UI", 8.5F), Margin = Padding.Empty };
            task.Controls.Add(log, 0, 3); outer.Controls.Add(task, 0, 3);
            location = Label("游戏目录只读。各游戏输出独立保存；请保持相邻的 tools 文件夹完整。");
            location.ForeColor = Color.FromArgb(105, 119, 138); outer.Controls.Add(location, 0, 4);
        }

        Dictionary<string, object> ReadJson(string path) {
            return File.Exists(path) ? json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)) : new Dictionary<string, object>();
        }
        static string Value(Dictionary<string, object> data, string key, string fallback = "") {
            object value; return data.TryGetValue(key, out value) && value != null ? Convert.ToString(value) : fallback;
        }
        static bool Flag(Dictionary<string, object> data, string key) { object v; return data.TryGetValue(key, out v) && v is bool && (bool)v; }
        string ConfigPath(string text) {
            text = text.Trim();
            if (text.Length == 0) throw new ArgumentException("游戏目录和 Blender 路径不能为空。");
            return Path.IsPathRooted(text) ? Path.GetFullPath(text) : Path.GetFullPath(Path.Combine(root, text));
        }
        void LoadConfig() {
            Dictionary<string, object> data = ReadJson(Path.Combine(root, "profiles.json"));
            if (!data.ContainsKey("profiles")) throw new InvalidDataException("找不到游戏配置，请保留 profiles.json。");
            blender.Text = ConfigPath(Value(data, "blender", "C:/Program Files/Blender Foundation/Blender 4.5/blender.exe"));
            decimal n; if (Decimal.TryParse(Value(data, "workers", "8"), out n)) workers.Value = Math.Max(1, Math.Min(32, n));
            populating = true;
            HashSet<string> seen = new HashSet<string>();
            foreach (object raw in (System.Collections.IEnumerable)data["profiles"]) {
                Dictionary<string, object> profile = (Dictionary<string, object>)raw;
                string id = Value(profile, "id");
                if (!System.Text.RegularExpressions.Regex.IsMatch(id, "^[A-Za-z0-9_-]+$") || !seen.Add(id))
                    throw new InvalidDataException("游戏配置 ID 不合法或重复：" + id);
                if (String.IsNullOrWhiteSpace(Value(profile, "output")) || String.IsNullOrWhiteSpace(Value(profile, "game")))
                    throw new InvalidDataException("游戏配置缺少安装路径或输出目录：" + id);
                profiles.Items.Add(new ProfileRow { Data = profile });
            }
            if (profiles.Items.Count == 0) throw new InvalidDataException("尚未提供可用的游戏配置。");
            populating = false;
            string last = Value(ReadJson(Path.Combine(root, "gui_state.json")), "profile_id");
            profiles.SelectedItem = profiles.Items.Cast<ProfileRow>().FirstOrDefault(p => p.Id == last) ?? profiles.Items[0];
        }
        Dictionary<string, object> CaptureProfile() {
            return new Dictionary<string, object> {
                { "scope", allModels.Checked ? "all" : "selected" }, { "selected_ids", selected.OrderBy(x => x).ToArray() },
                { "search", search.Text }, { "family", family.SelectedItem == null ? "全部类别" : family.SelectedItem.ToString() },
                { "rescan", rescan.Checked }, { "rebuild_intermediate", rebuild.Checked }, { "game", game.Text }
            };
        }
        void ActivateProfile() {
            if (busy) return;
            ProfileRow chosen = profiles.SelectedItem as ProfileRow;
            if (chosen == null || chosen == currentProfile) return;
            if (currentProfile != null) profileStates[currentProfile.Id] = CaptureProfile();
            currentProfile = chosen;
            Dictionary<string, object> prefs;
            if (!profileStates.TryGetValue(chosen.Id, out prefs)) prefs = ReadJson(Path.Combine(root, "gui_settings", chosen.Id + ".json"));
            populating = true;
            // The profile is authoritative for disk-loaded paths; preserve only in-session unsaved edits.
            game.Text = ConfigPath(profileStates.ContainsKey(chosen.Id) ? Value(prefs, "game", Value(chosen.Data, "game")) : Value(chosen.Data, "game"));
            outputPath.Text = OutputRoot;
            selected.Clear(); object ids;
            if (prefs.TryGetValue("selected_ids", out ids)) foreach (object id in (System.Collections.IEnumerable)ids) selected.Add(Convert.ToString(id));
            search.Text = Value(prefs, "search"); family.Items.Clear();
            rescan.Checked = Flag(prefs, "rescan"); rebuild.Checked = Flag(prefs, "rebuild_intermediate");
            allModels.Checked = Value(prefs, "scope", "all") != "selected"; selectedModels.Checked = !allModels.Checked;
            models.Clear(); modelList.Items.Clear();
            populating = false; log.Clear(); progress.Value = 0; taskLog = null;
            LoadCatalog();
            string wanted = Value(prefs, "family", "全部类别"); if (family.Items.Contains(wanted)) family.SelectedItem = wanted;
            phase.Text = chosen + (models.Count == 0 ? " · 首次使用请先扫描模型目录。" : " · 已加载模型目录，可以选择导出。");
        }
        Dictionary<string, object> Request(bool checkOnly, string action = "export") {
            if (currentProfile == null) throw new InvalidOperationException("请先选择游戏配置。");
            return new Dictionary<string, object> {
                { "profile_id", currentProfile.Id }, { "game", ConfigPath(game.Text) }, { "output", OutputRoot },
                { "blender", ConfigPath(blender.Text) }, { "workers", (int)workers.Value },
                { "locale", Value(currentProfile.Data, "locale") }, { "action", action },
                { "rescan", rescan.Checked }, { "rebuild_intermediate", rebuild.Checked },
                { "model_ids", checkOnly || action == "scan" || allModels.Checked ? null : (object)selected.OrderBy(x => x).ToArray() }
            };
        }
        void SaveSettings() {
            Dictionary<string, object> request = Request(true);
            Dictionary<string, object> data = ReadJson(Path.Combine(root, "profiles.json"));
            bool found = false;
            foreach (object raw in (System.Collections.IEnumerable)data["profiles"]) {
                Dictionary<string, object> profile = (Dictionary<string, object>)raw;
                if (Value(profile, "id") == currentProfile.Id) { profile["game"] = request["game"]; found = true; }
            }
            if (!found) throw new InvalidDataException("当前游戏配置已被外部修改，请重新打开工具。");
            data["blender"] = request["blender"]; data["workers"] = request["workers"];
            WriteJson(Path.Combine(root, "profiles.json"), data);
            currentProfile.Data["game"] = request["game"];
            Dictionary<string, object> prefs = CaptureProfile();
            profileStates[currentProfile.Id] = prefs;
            WriteJson(Path.Combine(root, "gui_settings", currentProfile.Id + ".json"), prefs);
            WriteJson(Path.Combine(root, "gui_state.json"), new Dictionary<string, object> { { "profile_id", currentProfile.Id } });
        }
        void WriteJson(string path, object value) {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temp = path + ".tmp";
            File.WriteAllText(temp, json.Serialize(value), new UTF8Encoding(false));
            if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path);
        }
        static IEnumerable<string[]> Csv(string path) {
            // Handles quoted commas and escaped quotes; current catalogs contain single-line fields.
            foreach (string line in File.ReadLines(path, Encoding.UTF8)) {
                List<string> fields = new List<string>(); StringBuilder field = new StringBuilder(); bool quoted = false;
                for (int i = 0; i < line.Length; i++) {
                    char ch = line[i];
                    if (ch == '"') { if (quoted && i + 1 < line.Length && line[i + 1] == '"') { field.Append('"'); i++; } else quoted = !quoted; }
                    else if (ch == ',' && !quoted) { fields.Add(field.ToString()); field.Length = 0; }
                    else field.Append(ch);
                }
                fields.Add(field.ToString()); yield return fields.ToArray();
            }
        }
        void LoadCatalog() {
            if (modelList == null || currentProfile == null) return;
            try {
                Dictionary<string, string> bones = new Dictionary<string, string>();
                string fbxCatalog = Path.Combine(OutputRoot, "reports", "fbx_catalog.csv");
                if (File.Exists(fbxCatalog)) foreach (string[] row in Csv(fbxCatalog).Skip(1)) if (row.Length > 7) bones[row[0]] = row[7];
                models.Clear();
                string catalog = Path.Combine(OutputRoot, "reports", "model_catalog.csv");
                if (File.Exists(catalog)) foreach (string[] row in Csv(catalog).Skip(1)) {
                    if (row.Length < 6) continue;
                    string b; bones.TryGetValue(row[0], out b);
                    models.Add(new ModelRow { Id = row[0], Family = row[1], Bones = b ?? "—", Materials = row[3], Textures = row[5],
                        State = File.Exists(Path.Combine(OutputRoot, "fbx", row[0] + ".fbx")) ? "已有导出" : "待导出" });
                }
                selected.IntersectWith(models.Select(m => m.Id));
                populating = true;
                string oldFamily = family.SelectedItem == null ? "全部类别" : family.SelectedItem.ToString();
                family.Items.Clear(); family.Items.Add("全部类别");
                foreach (string f in models.Select(m => m.Family).Distinct().OrderBy(s => s)) family.Items.Add(f);
                family.SelectedItem = family.Items.Contains(oldFamily) ? oldFamily : "全部类别";
                populating = false; FilterCatalog();
                if (models.Count == 0) AppendLog("尚无模型索引。请点击「扫描模型目录」，完成后在列表中勾选需要的模型。");
            } catch (Exception ex) { populating = false; AppendLog("模型列表读取失败：" + ex.Message); }
        }
        void FilterCatalog() {
            if (modelList == null || populating) return;
            string[] words = search.Text.Trim().ToLowerInvariant().Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            string f = family.SelectedItem == null ? "全部类别" : family.SelectedItem.ToString();
            populating = true; modelList.BeginUpdate(); modelList.Items.Clear();
            foreach (ModelRow row in models) {
                string haystack = (row.Id + " " + row.Family).ToLowerInvariant();
                if ((f != "全部类别" && row.Family != f) || words.Any(w => !haystack.Contains(w))) continue;
                ListViewItem item = new ListViewItem(new string[] { row.Id, row.Family, row.Bones, row.Materials, row.Textures, row.State });
                item.Checked = selected.Contains(row.Id); modelList.Items.Add(item);
            }
            modelList.EndUpdate(); populating = false; UpdateCount();
        }
        void SelectVisible() {
            foreach (ListViewItem item in modelList.Items) selected.Add(item.Text);
            selectedModels.Checked = true; FilterCatalog();
        }
        void UpdateCount() {
            if (count == null || allModels == null) return;
            count.Text = String.Format("共 {0:N0} 个模型 · 当前显示 {1:N0} · 已勾选 {2:N0}", models.Count, modelList.Items.Count, selected.Count);
            allModels.Text = String.Format("全部模型（{0:N0}）", models.Count);
            start.Text = allModels.Checked ? "开始导出全部模型" : String.Format("导出所选（{0}）", selected.Count);
            start.Enabled = !busy && currentProfile != null && models.Count > 0 && (allModels.Checked || selected.Count > 0);
        }
        void BrowseGame() {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog { Description = "选择当前游戏的安装目录", SelectedPath = game.Text, ShowNewFolderButton = false })
                if (dialog.ShowDialog(this) == DialogResult.OK) game.Text = dialog.SelectedPath;
        }
        void BrowseBlender() {
            using (OpenFileDialog dialog = new OpenFileDialog { Title = "选择 Blender 4.5 LTS 或 5.2 LTS", Filter = "Blender 程序|blender.exe|程序文件|*.exe", FileName = blender.Text })
                if (dialog.ShowDialog(this) == DialogResult.OK) blender.Text = dialog.FileName;
        }
        void SetBusy(bool value) {
            busy = value; foreach (Control control in mutable) control.Enabled = !value;
            cancel.Enabled = value; UpdateCount();
        }
        void BeginTask(bool checkOnly, string action = "export") {
            if (busy) return;
            try {
                if (!checkOnly && action == "export" && !allModels.Checked && selected.Count == 0) throw new InvalidOperationException("请先勾选至少一个模型，或选择全部模型。");
                string python = PythonPath;
                string backend = BackendPath;
                if (!File.Exists(python) || !File.Exists(backend)) throw new FileNotFoundException("工具文件不完整。请保持 fdk_toolkit 与 tools 目录相邻，并保留 tool 文件夹。");
                string run = Path.Combine(root, "reports", "gui_runs", DateTime.Now.ToString("yyyyMMdd_HHmmss_fff"));
                Directory.CreateDirectory(run);
                string request = Path.Combine(run, "request.json"); cancelFile = Path.Combine(run, "cancel.json");
                taskLog = Path.Combine(run, "ui_events.log");
                WriteJson(request, Request(checkOnly, action));
                SaveSettings();
                log.Clear(); progress.Value = 0; checking = checkOnly; doneReceived = false; doneStatus = null; lastError = null;
                phase.Text = checkOnly ? "正在检查路径和依赖…" : action == "scan" ? "正在扫描模型目录…" : "准备导出…";
                ProcessStartInfo info = new ProcessStartInfo(python, "-X utf8 -u " + Q(backend) + " --request " + Q(request) + " --cancel-file " + Q(cancelFile) + (checkOnly ? " --check-only" : ""));
                info.WorkingDirectory = root; info.UseShellExecute = false; info.CreateNoWindow = true;
                info.RedirectStandardOutput = true; info.RedirectStandardError = true;
                info.StandardOutputEncoding = Encoding.UTF8; info.StandardErrorEncoding = Encoding.UTF8;
                info.EnvironmentVariables["PYTHONUTF8"] = "1"; info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                info.EnvironmentVariables.Remove("PYTHONHOME"); info.EnvironmentVariables.Remove("PYTHONPATH");
                process = new Process { StartInfo = info, EnableRaisingEvents = true };
                Process active = process;
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Post(delegate { Consume(e.Data); }); };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Post(delegate { AppendLog(e.Data); }); };
                process.Exited += delegate { active.WaitForExit(); int code = active.ExitCode; Post(delegate { Finish(code); }); };
                job = new NativeJob();
                SetBusy(true);
                process.Start();
                try { job.Attach(process); } catch { process.Kill(); throw; }
                process.BeginOutputReadLine(); process.BeginErrorReadLine();
                AppendLog("当前游戏：" + currentProfile + "；输出：" + OutputRoot);
                AppendLog(checkOnly ? "环境检查已开始。" : action == "scan" ? "仅扫描资源目录，不导出模型。" : (allModels.Checked ? "导出范围：全部模型。" : "导出范围：" + selected.Count + " 个勾选模型。"));
            } catch (Exception ex) {
                if (job != null) { job.Dispose(); job = null; }
                SetBusy(false); phase.Text = "无法开始任务"; Error(ex.Message);
            }
        }
        void Post(Action action) {
            if (IsDisposed || !IsHandleCreated) return;
            try { BeginInvoke(action); } catch (InvalidOperationException) { }
        }
        void Consume(string line) {
            try {
                Dictionary<string, object> evt = json.Deserialize<Dictionary<string, object>>(line);
                string type = Value(evt, "type"), message = Value(evt, "message");
                string eventLog = Value(evt, "log");
                if (!String.IsNullOrEmpty(eventLog)) taskLog = eventLog;
                if (type == "progress") {
                    double total, current;
                    if (Double.TryParse(Value(evt, "total"), out total) && total > 0 && Double.TryParse(Value(evt, "current"), out current))
                        progress.Value = (int)Math.Max(0, Math.Min(100, 100 * current / total));
                }
                if (type == "status" || type == "progress") phase.Text = message;
                if (type == "error") { lastError = message; phase.Text = "任务遇到问题"; }
                if (type == "done") {
                    doneReceived = true;
                    doneStatus = Value(evt, "status");
                    if (Flag(evt, "success")) progress.Value = 100;
                    phase.Text = message;
                    string path = Value(evt, "log"); if (!String.IsNullOrEmpty(path)) taskLog = path;
                }
                if (!String.IsNullOrEmpty(message)) AppendLog(message);
            } catch { AppendLog(line); }
        }
        void AppendLog(string message) {
            if (log == null) return;
            if (log.TextLength > 120000) log.Text = log.Text.Substring(log.TextLength - 70000);
            log.AppendText(message.TrimEnd() + Environment.NewLine);
            log.SelectionStart = log.TextLength; log.ScrollToCaret();
        }
        void Finish(int code) {
            lastExitCode = code;
            if (job != null) { job.Dispose(); job = null; }
            if (!doneReceived) phase.Text = code == 0 ? "任务结束，但缺少完成报告，请检查日志。" : "任务中断，请检查日志；再次开始可续跑。";
            if (!String.IsNullOrEmpty(lastError)) phase.Text = "未完成：" + lastError;
            try {
                string events = Path.Combine(Path.GetDirectoryName(cancelFile), "ui_events.log");
                File.WriteAllText(events, log.Text, new UTF8Encoding(false));
                if (!File.Exists(taskLog)) taskLog = events;
            } catch { }
            if (process != null) { process.Dispose(); process = null; }
            SetBusy(false); if (!checking) LoadCatalog();
            if (closeWhenDone) Close();
        }
        void CancelTask() {
            if (!busy) return;
            try {
                File.WriteAllText(cancelFile, "{}", new UTF8Encoding(false));
                cancel.Enabled = false; phase.Text = "正在停止任务并保存状态，请稍候…";
                AppendLog("已请求停止。已经完成的文件会保留，可再次续跑。");
            } catch (Exception ex) { Error("无法发送停止请求：" + ex.Message); }
        }
        void OnClosing(object sender, FormClosingEventArgs e) {
            if (!busy) return;
            e.Cancel = true;
            if (closeWhenDone) return;
            if (MessageBox.Show(this, "任务仍在运行。停止本次任务并关闭窗口？已经完成的文件会保留。", "停止并关闭", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes) {
                closeWhenDone = true; CancelTask();
            }
        }
        void Error(string message) {
            AppendLog(message);
            if (integrationTesting) throw new InvalidOperationException(message);
            MessageBox.Show(this, message, "资源工具", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
        void OpenPath(string path) {
            try { if (!Directory.Exists(path) && !File.Exists(path)) throw new FileNotFoundException("尚未生成：" + path); Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception ex) { Error(ex.Message); }
        }
        void Reveal(string path) {
            if (!File.Exists(path)) { Error("找不到文件：" + path); return; }
            Process.Start(new ProcessStartInfo("explorer.exe", "/select," + Q(path)) { UseShellExecute = true });
        }
        void OpenBlender() {
            try {
                string executable = ConfigPath(blender.Text);
                if (!File.Exists(executable)) throw new FileNotFoundException("请先选择有效的 Blender 程序。");
                Process.Start(new ProcessStartInfo(executable) { WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true });
            } catch (Exception ex) { Error(ex.Message); }
        }
        void ShowHelp(string file = "README.md") {
            try {
                string body = File.ReadAllText(Path.Combine(root, file), Encoding.UTF8);
                using (Form viewer = new Form { Text = "使用说明", Width = 850, Height = 720, StartPosition = FormStartPosition.CenterParent, Font = Font }) {
                    viewer.Controls.Add(new TextBox { Text = body.Replace("\r\n", "\n").Replace("\n", "\r\n"), Multiline = true, ReadOnly = true,
                        ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill, BackColor = Color.White, BorderStyle = BorderStyle.None, Font = new Font(Font.FontFamily, 10) });
                    viewer.ShowDialog(this);
                }
            } catch (Exception ex) { Error(ex.Message); }
        }
        static string Q(string value) {
            // Windows CommandLineToArgvW escaping, including quoted/trailing backslashes.
            StringBuilder s = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { s.Append('\\', slashes * 2 + 1); s.Append(c); slashes = 0; continue; }
                s.Append('\\', slashes); slashes = 0; s.Append(c);
            }
            s.Append('\\', slashes * 2); s.Append('"'); return s.ToString();
        }
        Dictionary<string, object> PortableProbe(string arguments, int expectedExit) {
            ProcessStartInfo info = new ProcessStartInfo(PythonPath, "-X utf8 -u " + arguments) {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables.Remove("PYTHONHOME"); info.EnvironmentVariables.Remove("PYTHONPATH");
            info.EnvironmentVariables["PYTHONUTF8"] = "1"; info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            StringBuilder output = new StringBuilder(), error = new StringBuilder();
            using (NativeJob owner = new NativeJob())
            using (Process probe = new Process { StartInfo = info }) {
                probe.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) output.AppendLine(e.Data); };
                probe.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) error.AppendLine(e.Data); };
                probe.Start();
                try { owner.Attach(probe); } catch { probe.Kill(); throw; }
                probe.BeginOutputReadLine(); probe.BeginErrorReadLine();
                if (!probe.WaitForExit(30000)) throw new TimeoutException("Portable runtime probe timed out");
                probe.WaitForExit();
                if (probe.ExitCode != expectedExit) throw new Exception("Portable runtime probe failed: " + output + error);
                return new Dictionary<string, object> { { "exit_code", probe.ExitCode }, { "stdout", output.ToString() }, { "stderr", error.ToString() } };
            }
        }
        public int PortableTest(string reportPath) {
            // No game assets, export or preference writes. Run against an unpacked release copy.
            ShowInTaskbar = false; Opacity = 0; Show(); Application.DoEvents(); PerformLayout();
            reportPath = Path.GetFullPath(reportPath);
            string destination = Path.GetDirectoryName(reportPath);
            Directory.CreateDirectory(destination);
            if (profiles.Items.Count < 2) throw new Exception("Expected the two supported game profiles");
            if (!File.Exists(PythonPath) || !File.Exists(BackendPath)) throw new FileNotFoundException("Portable runtime or backend is missing");
            byte[] originalConfig = File.ReadAllBytes(Path.Combine(root, "profiles.json"));
            HashSet<string> outputs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (ProfileRow profile in profiles.Items) {
                profiles.SelectedItem = profile;
                Dictionary<string, object> request = Request(true, "scan");
                if (Value(request, "profile_id") != profile.Id || !outputs.Add(OutputRoot)) throw new Exception("Profile output isolation failed");
                if (!Path.IsPathRooted(Value(request, "game")) || !Path.IsPathRooted(OutputRoot)) throw new Exception("Relative configuration resolution failed");
            }
            SetBusy(true);
            if (check.Enabled || profiles.Enabled || !cancel.Enabled) throw new Exception("Busy controls are inconsistent");
            SetBusy(false);
            Dictionary<string, object> entry = PortableProbe(Q(BackendPath) + " --help", 0);
            if (!Value(entry, "stdout").Contains("--check-only")) throw new Exception("Backend entry point did not respond");
            string code = "import sys; sys.path.insert(0, " + json.Serialize(Path.Combine(root, "tool")) + "); " +
                "import json,numpy,PIL,zstandard,blowfish,xxhash,pyquaternion,lz4.frame,fdk_assets,fdk_backend; " +
                "assert sys.flags.utf8_mode == 1; " +
                "print(json.dumps({'python':sys.version.split()[0], 'utf8_mode':sys.flags.utf8_mode, 'runtime':sys.executable, 'backend':fdk_backend.__file__}))";
            Dictionary<string, object> imports = PortableProbe("-c " + Q(code), 0);
            string requestPath = Path.Combine(destination, "portable-request-" + Guid.NewGuid().ToString("N") + ".json");
            string missingGame = Path.Combine(destination, "missing-game-" + Guid.NewGuid().ToString("N"));
            Dictionary<string, object> preflight;
            profiles.SelectedItem = profiles.Items.Cast<ProfileRow>().Single(p => p.Id == "kuro2");
            Dictionary<string, object> checkRequest = Request(true, "scan");
            checkRequest["game"] = missingGame;
            checkRequest["output"] = Path.Combine(root, "reports", "portable-empty-" + Guid.NewGuid().ToString("N"));
            try {
                WriteJson(requestPath, checkRequest);
                preflight = PortableProbe(Q(BackendPath) + " --request " + Q(requestPath) + " --check-only", 1);
                Dictionary<string, object>[] events = Value(preflight, "stdout").Split(new string[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(line => json.Deserialize<Dictionary<string, object>>(line)).ToArray();
                Dictionary<string, object> done = events.Last();
                if (Value(done, "type") != "done" || Flag(done, "success") || Value(done, "status") != "failed" ||
                    !Value(done, "message").Contains(missingGame)) throw new Exception("Backend did not reject the synthetic missing game as expected");
                if (Directory.Exists(Value(checkRequest, "output"))) throw new Exception("Check-only probe created an output library");
            } finally { if (File.Exists(requestPath)) File.Delete(requestPath); }
            if (!File.ReadAllBytes(Path.Combine(root, "profiles.json")).SequenceEqual(originalConfig)) throw new Exception("Portable check modified configuration");
            WriteJson(reportPath, new { passed = true, version = "1.3.0", executable_root = root, backend = BackendPath,
                profiles_checked = profiles.Items.Cast<ProfileRow>().Select(p => p.Id).ToArray(),
                dependencies = imports, entry_point = entry, missing_game_preflight = preflight,
                tests = new string[] { "hidden_gui_startup", "profile_output_isolation", "relative_paths", "busy_controls",
                    "backend_entry_point", "runtime_imports", "preflight_child_process", "check_only_does_not_export", "configuration_unchanged" } });
            return 0;
        }
        public int SelfTest(string reportPath) {
            // Exercise the actual form/controller without changing user configuration or running an export.
            ShowInTaskbar = false; Opacity = 0; Show(); Application.DoEvents(); PerformLayout();
            if (profiles.Items.Count < 2) throw new Exception("Two profiles are required for isolation verification");
            ProfileRow first = profiles.Items.Cast<ProfileRow>().First(p => File.Exists(Path.Combine(ConfigPath(Value(p.Data, "output")), "reports", "model_catalog.csv")));
            profiles.SelectedItem = first;
            if (models.Count < 1) throw new Exception("Catalog is empty");
            HashSet<string> original = new HashSet<string>(selected);
            string firstOutput = OutputRoot;
            int firstCount = models.Count;
            search.Text = "chr0000";
            if (modelList.Items.Count == 0 || modelList.Items.Cast<ListViewItem>().Any(i => !i.Text.Contains("chr0000"))) throw new Exception("Search mismatch");
            selected.Clear(); SelectVisible();
            string[] ids = (string[])Request(false)["model_ids"];
            if (ids.Length != modelList.Items.Count || allModels.Checked) throw new Exception("Selection scope mismatch");
            search.Text = "mon0000";
            if (selected.Count != ids.Length) throw new Exception("Filtering lost selection");
            ProfileRow second = profiles.Items.Cast<ProfileRow>().First(p => p != first);
            profileStates[second.Id] = new Dictionary<string, object>();
            profiles.SelectedItem = second;
            if (selected.Count != 0 || OutputRoot == firstOutput || game.Text == ConfigPath(Value(first.Data, "game"))) throw new Exception("Profile state leaked across games");
            Dictionary<string, object> scanRequest = Request(false, "scan");
            if ((string)scanRequest["profile_id"] != second.Id || (string)scanRequest["output"] != OutputRoot || scanRequest["model_ids"] != null || (string)scanRequest["action"] != "scan")
                throw new Exception("Scan request targets the wrong game or model subset");
            profiles.SelectedItem = first;
            if (selected.Count != ids.Length || OutputRoot != firstOutput || models.Count != firstCount || !selectedModels.Checked)
                throw new Exception("Profile state did not restore");
            allModels.Checked = true;
            if (Request(false)["model_ids"] != null) throw new Exception("All-model request mismatch");
            string savedGame = game.Text;
            game.Text = "..\\relative-game";
            if ((string)Request(true)["game"] != Path.GetFullPath(Path.Combine(root, "..\\relative-game"))) throw new Exception("Relative path resolution mismatch");
            game.Text = " "; bool rejected = false;
            try { Request(true); } catch (ArgumentException) { rejected = true; }
            if (!rejected) throw new Exception("Blank game path accepted");
            game.Text = savedGame;
            if (Q("a\\\"b\\") != "\"a\\\\\\\"b\\\\\"") throw new Exception("Argument quoting mismatch");
            selected.Clear(); foreach (string id in original) selected.Add(id);
            search.Text = ""; allModels.Checked = true; FilterCatalog();
            foreach (Control control in mutable) if (!control.Enabled) throw new Exception("Control disabled at idle");
            SetBusy(true); if (start.Enabled || !cancel.Enabled || game.Enabled || profiles.Enabled || scan.Enabled) throw new Exception("Busy state mismatch");
            SetBusy(false);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath)));
            string preferencesFixture = TestPreferencesIsolation(Path.GetDirectoryName(Path.GetFullPath(reportPath)));
            string screenshot = Path.ChangeExtension(reportPath, ".png");
            using (Bitmap bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(screenshot, System.Drawing.Imaging.ImageFormat.Png); }
            File.WriteAllText(reportPath, json.Serialize(new { passed = true, catalog_count = models.Count,
                profiles_checked = new string[] { first.Id, second.Id }, scan_profile = scanRequest["profile_id"], scan_output = scanRequest["output"],
                selection_count_tested = ids.Length, preferences_fixture = preferencesFixture,
                tests = new string[] { "catalog", "search", "selection_persistence", "profile_state_isolation", "scan_request", "request_scope", "relative_paths", "blank_paths", "argument_quoting", "busy_controls", "preferences_save_preserves_other_profiles", "preferences_reload" }, screenshot = screenshot }), new UTF8Encoding(false));
            return 0;
        }
        string TestPreferencesIsolation(string destination) {
            // Preference writes are exercised only in a fresh fixture, never against the live profile file.
            string fixture = Path.Combine(destination, "preferences_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(fixture);
            Dictionary<string, object> data = ReadJson(Path.Combine(root, "profiles.json"));
            foreach (object raw in (System.Collections.IEnumerable)data["profiles"]) {
                Dictionary<string, object> p = (Dictionary<string, object>)raw;
                p["game"] = ConfigPath(Value(p, "game")); p["output"] = ConfigPath(Value(p, "output"));
            }
            WriteJson(Path.Combine(fixture, "profiles.json"), data);
            string chosenId, rememberedId;
            using (ExporterForm probe = new ExporterForm(fixture)) {
                ProfileRow chosen = probe.profiles.Items.Cast<ProfileRow>().First(p => File.Exists(Path.Combine(Value(p.Data, "output"), "reports", "model_catalog.csv")));
                probe.profiles.SelectedItem = chosen; chosenId = chosen.Id;
                ProfileRow other = probe.profiles.Items.Cast<ProfileRow>().First(p => p != chosen);
                string untouched = json.Serialize(other.Data);
                rememberedId = probe.models[0].Id;
                probe.selected.Clear(); probe.selected.Add(rememberedId); probe.selectedModels.Checked = true;
                probe.game.Text += "_gui_preferences_test"; probe.rescan.Checked = true;
                probe.SaveSettings();
                Dictionary<string, object> saved = ReadJson(Path.Combine(fixture, "profiles.json"));
                Dictionary<string, object> savedOther = ((System.Collections.IEnumerable)saved["profiles"]).Cast<Dictionary<string, object>>().First(p => Value(p, "id") == other.Id);
                if (json.Serialize(savedOther) != untouched) throw new Exception("Saving one profile modified another profile");
                if (File.Exists(Path.Combine(fixture, "gui_settings", other.Id + ".json"))) throw new Exception("Saving one profile wrote another selection file");
            }
            using (ExporterForm reopened = new ExporterForm(fixture)) {
                if (reopened.currentProfile.Id != chosenId || !reopened.selected.SetEquals(new string[] { rememberedId }) || !reopened.selectedModels.Checked || !reopened.rescan.Checked)
                    throw new Exception("Saved profile selection did not survive reopening");
                if (!reopened.game.Text.EndsWith("_gui_preferences_test")) throw new Exception("Saved game directory was not restored");
            }
            return fixture;
        }
        public int IntegrationTest(string reportPath) {
            // Runs actual form handlers against the real backend, with preferences restored byte-for-byte.
            reportPath = Path.GetFullPath(reportPath);
            string destination = Path.GetDirectoryName(reportPath);
            Directory.CreateDirectory(destination);
            string settingsDirectory = Path.Combine(root, "gui_settings");
            bool hadSettingsDirectory = Directory.Exists(settingsDirectory);
            string[] preferencePaths = new string[] {
                Path.Combine(root, "profiles.json"), Path.Combine(root, "gui_state.json"),
                Path.Combine(settingsDirectory, "kuro2.json"), Path.Combine(settingsDirectory, "sky2.json")
            };
            Dictionary<string, byte[]> backup = preferencePaths.ToDictionary(p => p, p => File.Exists(p) ? File.ReadAllBytes(p) : null);
            List<object> checks = new List<object>();
            DateTime began = DateTime.UtcNow;
            string failure = null, screenshot = Path.ChangeExtension(reportPath, ".png");
            integrationTesting = true;
            try {
                ShowInTaskbar = false; Opacity = 0; Show(); Application.DoEvents();
                foreach (string profileId in new string[] { "kuro2", "sky2" }) {
                    SelectTestProfile(profileId);
                    BeginTask(true); WaitForTask(120); RequireTask("ready", 0);
                    checks.Add(TaskEvidence("environment", profileId));
                }
                SelectTestProfile("sky2");
                Dictionary<string, string> beforeScan = SnapshotFbx(OutputRoot);
                // Invoke the real button. It must generate a scan request and never export the full set.
                scan.PerformClick(); WaitForTask(240); RequireTask("complete", 0);
                Dictionary<string, object> scanRequest = ReadJson(Path.Combine(Path.GetDirectoryName(cancelFile), "request.json"));
                if (Value(scanRequest, "action") != "scan" || scanRequest["model_ids"] != null)
                    throw new Exception("Scan button did not send a scan-only request");
                Dictionary<string, string> afterScan = SnapshotFbx(OutputRoot);
                if (beforeScan.Count != afterScan.Count || beforeScan.Any(p => !afterScan.ContainsKey(p.Key) || afterScan[p.Key] != p.Value))
                    throw new Exception("Scan-only action changed the FBX file set");
                Dictionary<string, object> scanEvidence = TaskEvidence("scan_only", "sky2");
                scanEvidence["fbx_unchanged"] = true; scanEvidence["catalog_models"] = models.Count;
                checks.Add(scanEvidence);
                foreach (string profileId in new string[] { "sky2", "kuro2" }) {
                    SelectTestProfile(profileId);
                    string id = profileId == "sky2" ? "chr5002" : "chr0000";
                    if (!models.Any(m => m.Id == id)) throw new Exception("Test model missing from profile catalog: " + profileId + "/" + id);
                    selected.Clear(); selected.Add(id); selectedModels.Checked = true; FilterCatalog();
                    string modelPath = Path.Combine(OutputRoot, "fbx", id + ".fbx");
                    string before = File.Exists(modelPath) ? FbxStamp(modelPath) : null;
                    start.PerformClick(); WaitForTask(300); RequireTask("complete", 0);
                    if (!File.Exists(modelPath)) throw new Exception("Selected model was not exported: " + modelPath);
                    bool unchanged = before == FbxStamp(modelPath);
                    if (profileId == "kuro2" && !unchanged) throw new Exception("Existing Kuro II FBX was unnecessarily rewritten");
                    Dictionary<string, object> evidence = TaskEvidence("selected_export", profileId);
                    evidence["model_id"] = id; evidence["fbx_unchanged"] = unchanged;
                    checks.Add(evidence);
                }
                SelectTestProfile("sky2");
                BeginTask(true); cancel.PerformClick(); WaitForTask(120); RequireTask("cancelled", 130);
                checks.Add(TaskEvidence("cancel_preflight", "sky2"));
                search.Text = "chr5002"; FilterCatalog(); phase.Text = "跨游戏图形界面验证完成：扫描、所选导出、取消均正常。";
                Application.DoEvents(); PerformLayout();
                using (Bitmap bmp = new Bitmap(Width, Height)) {
                    DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(screenshot, System.Drawing.Imaging.ImageFormat.Png);
                }
            } catch (Exception ex) { failure = ex.ToString(); }
            finally {
                if (job != null) { job.Dispose(); job = null; }
                if (process != null) {
                    try { process.WaitForExit(10000); Application.DoEvents(); } catch { }
                }
                foreach (KeyValuePair<string, byte[]> entry in backup) {
                    if (entry.Value == null) { if (File.Exists(entry.Key)) File.Delete(entry.Key); }
                    else File.WriteAllBytes(entry.Key, entry.Value);
                }
                if (!hadSettingsDirectory && Directory.Exists(settingsDirectory) && !Directory.EnumerateFileSystemEntries(settingsDirectory).Any())
                    Directory.Delete(settingsDirectory);
                integrationTesting = false;
            }
            bool restored = backup.All(p => p.Value == null ? !File.Exists(p.Key) : File.Exists(p.Key) && File.ReadAllBytes(p.Key).SequenceEqual(p.Value));
            if (!restored && failure == null) failure = "Preferences were not restored byte-for-byte";
            WriteJson(reportPath, new { passed = failure == null, started_utc = began.ToString("o"), seconds = (DateTime.UtcNow - began).TotalSeconds,
                checks = checks, preferences_restored = restored, error = failure, screenshot = File.Exists(screenshot) ? screenshot : null });
            return failure == null ? 0 : 1;
        }
        void SelectTestProfile(string id) {
            profiles.SelectedItem = profiles.Items.Cast<ProfileRow>().Single(p => p.Id == id);
            workers.Value = 1; rescan.Checked = false; rebuild.Checked = false;
            allModels.Checked = true; selected.Clear(); search.Text = ""; FilterCatalog();
        }
        void WaitForTask(int seconds) {
            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            while (busy && DateTime.UtcNow < deadline) { Application.DoEvents(); System.Threading.Thread.Sleep(20); }
            if (busy) throw new TimeoutException("GUI task timeout: " + phase.Text);
            if (!doneReceived) throw new Exception("Backend completion event missing: " + log.Text);
        }
        void RequireTask(string status, int code) {
            if (doneStatus != status || lastExitCode != code) throw new Exception("Unexpected task result " + doneStatus + "/" + lastExitCode + ": " + log.Text);
        }
        Dictionary<string, object> TaskEvidence(string checkName, string profileId) {
            return new Dictionary<string, object> { { "check", checkName }, { "profile", profileId }, { "status", doneStatus },
                { "exit_code", lastExitCode }, { "log", taskLog }, { "request", Path.Combine(Path.GetDirectoryName(cancelFile), "request.json") } };
        }
        static string FbxStamp(string path) {
            FileInfo file = new FileInfo(path);
            using (System.Security.Cryptography.SHA256 hash = System.Security.Cryptography.SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
                return file.Length + ":" + file.LastWriteTimeUtc.Ticks + ":" + BitConverter.ToString(hash.ComputeHash(stream));
        }
        static Dictionary<string, string> SnapshotFbx(string output) {
            string folder = Path.Combine(output, "fbx");
            return Directory.Exists(folder) ? Directory.GetFiles(folder, "*.fbx").ToDictionary(p => Path.GetFileName(p), p => FbxStamp(p)) : new Dictionary<string, string>();
        }
        protected override void Dispose(bool disposing) {
            if (disposing) { if (job != null) job.Dispose(); tips.Dispose(); }
            base.Dispose(disposing);
        }
    }

    internal sealed class NativeJob : IDisposable {
        [StructLayout(LayoutKind.Sequential)] struct Basic {
            public long ProcessTime, JobTime; public uint Flags; public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
        [StructLayout(LayoutKind.Sequential)] struct Extended {
            public Basic Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemory, PeakJobMemory;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref Extended info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        IntPtr handle;
        public NativeJob() {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            Extended info = new Extended(); info.Basic.Flags = 0x2000;
            if (!SetInformationJobObject(handle, 9, ref info, (uint)Marshal.SizeOf(info))) { Dispose(); throw new System.ComponentModel.Win32Exception(); }
        }
        public void Attach(Process process) { if (!AssignProcessToJobObject(handle, process.Handle)) throw new System.ComponentModel.Win32Exception(); }
        public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
    }
}

// 설정 창: 연결(비밀값) · 레포 · 채널 · 맥 운영(절전·자동 로그인) · 도메인·용어(보기만) · 정보.
// 저장은 모두 `askproject config set --json`(검증·원자적 쓰기, 비밀값은 Keychain).

import AppKit
import SwiftUI

enum SettingsTab: Hashable { case connections, repos, channels, host, domains, about }

@MainActor
final class SettingsModel: ObservableObject {
    @Published var config: ConfigState?
    @Published var note: CheckResult?
    @Published var working = false
    @Published var secrets: [String: String] = [:]
    @Published var repos: [RepoEntry] = []
    @Published var channels: [SlackChannel] = []
    @Published var channelsError: String?
    @Published var loadingChannels = false
    @Published var allowed: Set<String> = []
    @Published var allowDM = false
    @Published var hostChannel = ""
    @Published var escalation = ""  // 비면 운영 채널을 쓴다

    func load() async {
        let (c, out) = await Backend.json(ConfigState.self, ["config", "get", "--json"])
        config = c
        if c == nil { note = CheckResult(ok: false, detail: "설정을 읽지 못했어요: " + out.errorLine, step: nil) }
        repos = c?.repos ?? []
        allowedText = c?.settings.allowed_channels ?? ""
        hostChannel = c?.settings.host_channel ?? ""
        escalation = c?.settings.escalation_channel ?? ""
        if escalation == hostChannel { escalation = "" }
    }

    /// 쉼표 구분 문자열 ↔ 체크 상태. 'dm'은 DM 허용 표시.
    var allowedText: String {
        get { (Array(allowed).sorted() + (allowDM ? ["dm"] : [])).joined(separator: ",") }
        set {
            let ids = newValue.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            allowed = Set(ids.filter { $0 != "dm" })
            allowDM = ids.contains("dm")
        }
    }

    /// 불러온 채널 + 저장돼 있지만 목록에 없는 채널(봇이 나갔거나 보관된 채널)을 함께 보여 준다.
    var channelChoices: [SlackChannel] {
        let known = Set(channels.map(\.id))
        let saved = Set(allowed).union([hostChannel, escalation]).filter { !$0.isEmpty && !known.contains($0) }
        return channels + saved.sorted().map { SlackChannel(id: $0, name: "\($0) (목록에 없음)") }
    }

    func label(_ c: SlackChannel) -> String { channels.contains(c) ? "#\(c.name)" : c.name }

    func loadChannels() async {
        loadingChannels = true
        let (list, out) = await Backend.json(ChannelList.self, ["setup", "channels"])
        loadingChannels = false
        channels = list?.channels ?? []
        channelsError = list.map { $0.ok ? nil : $0.detail } ?? out.errorLine
    }

    func save(settings: [String: Any] = [:], secrets: [String: String] = [:], repos: [[String: String]]? = nil) async {
        working = true
        let r = await Backend.setConfig(settings: settings, secrets: secrets, repos: repos)
        working = false
        note = CheckResult(ok: r.ok, detail: r.ok ? "저장했어요. 실행 중인 봇에 적용하려고 다시 시작해요." : (r.error ?? "저장 실패"), step: nil)
        if r.ok {
            self.secrets = [:]
            AppModel.shared.restartBotIfRunning()
            await load()
        }
    }
}

struct SettingsView: View {
    @StateObject private var m = SettingsModel()
    @ObservedObject var app = AppModel.shared
    @State var tab: SettingsTab

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            TabView(selection: $tab) {
                connections.tabItem { Text("연결") }.tag(SettingsTab.connections)
                reposTab.tabItem { Text("레포") }.tag(SettingsTab.repos)
                channels.tabItem { Text("채널") }.tag(SettingsTab.channels)
                host.tabItem { Text("맥 운영") }.tag(SettingsTab.host)
                domains.tabItem { Text("도메인·용어") }.tag(SettingsTab.domains)
                about.tabItem { Text("정보") }.tag(SettingsTab.about)
            }
            .padding(16)
            if let n = m.note { NoteView(note: n).padding([.horizontal, .bottom], 16) }
        }
        .frame(width: 640, height: 560)
        .task { await m.load() }
    }

    private static let secretRows: [(key: String, title: String, prefix: String)] = [
        ("ASKPROJECT_SLACK_BOT_TOKEN", "슬랙 봇 토큰", "xoxb-"),
        ("ASKPROJECT_SLACK_APP_TOKEN", "슬랙 앱 토큰", "xapp-"),
        ("ASKPROJECT_GITHUB_TOKEN", "GitHub 토큰", "github_pat_"),
        ("ANTHROPIC_API_KEY", "Claude API 키", "sk-ant-"),
        ("ASKPROJECT_NOTION_TOKEN", "노션 통합 토큰 (선택)", ""),
    ]

    private var connections: some View {
        SettingsPage {
            SectionHeader("토큰과 키", "Keychain에 저장돼요. 바꿀 항목만 새로 붙여넣고 저장하세요.")
            ForEach(Self.secretRows, id: \.key) { row in
                let binding = Binding(get: { m.secrets[row.key] ?? "" }, set: { m.secrets[row.key] = $0 })
                TokenField(title: row.title, placeholder: row.prefix.isEmpty ? "" : row.prefix + "…", text: binding,
                           problem: row.prefix.isEmpty ? nil : WizardModel.prefixProblem(binding.wrappedValue, row.prefix, row.title),
                           saved: m.config?.secrets[row.key] ?? false)
                if row.key == "ASKPROJECT_GITHUB_TOKEN", let t = app.status?.github_token, let d = t.days_left {
                    HStack {
                        Text("만료까지 \(Int(d.rounded()))일").font(.caption).foregroundStyle(t.warning ? .orange : .secondary)
                        Button("새 토큰 만들기") {
                            Task {
                                let (u, _) = await Backend.json(URLResult.self, ["setup", "pat-url"])
                                if let u, let url = URL(string: u.url) { NSWorkspace.shared.open(url) }
                            }
                        }
                        .buttonStyle(.link).font(.caption)
                    }
                    .padding(.top, -6)
                }
            }
        } footer: {
            Spacer()
            Button("저장") {
                let filled = m.secrets.filter { !$0.value.isEmpty }.mapValues { $0.trimmingCharacters(in: .whitespaces) }
                Task { await m.save(secrets: filled) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(m.working || m.secrets.values.allSatisfy(\.isEmpty))
        }
    }

    private var reposTab: some View {
        SettingsPage {
            SectionHeader("읽을 레포", "현재는 첫 번째 레포만 답에 쓰여요. 바꾸면 다음 동기화 때 새로 받아요.")
            if m.repos.isEmpty {
                Text("등록된 레포가 없어요.").foregroundStyle(.secondary)
            }
            ForEach($m.repos) { $r in
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(alignment: .top, spacing: 10) {
                            LabeledField("이름", placeholder: "repo1", text: $r.name).frame(width: 140)
                            LabeledField("브랜치", placeholder: "main", text: $r.ref).frame(width: 120)
                            Spacer()
                            Button(role: .destructive) { m.repos.removeAll { $0.id == r.id } } label: {
                                Label("삭제", systemImage: "trash")
                            }
                            .buttonStyle(.borderless).foregroundStyle(.red).padding(.top, 18)
                        }
                        LabeledField("주소", placeholder: "https://github.com/조직/레포", text: $r.url)
                        LabeledField("한 줄 설명", placeholder: "무슨 서비스인지 (프롬프트에 들어가요)", text: $r.description)
                    }
                    .padding(6)
                }
            }
        } footer: {
            Button {
                m.repos.append(RepoEntry(name: "repo\(m.repos.count + 1)", url: "", ref: "main", description: ""))
            } label: { Label("레포 추가", systemImage: "plus") }
            Spacer()
            Button("저장") {
                let rows = m.repos.map { ["name": $0.name, "url": $0.url, "ref": $0.ref, "description": $0.description] }
                Task { await m.save(repos: rows) }
            }
            .buttonStyle(.borderedProminent).disabled(m.working || m.repos.isEmpty)
        }
    }

    private var channels: some View {
        SettingsPage {
            SectionHeader("답할 채널", "봇이 질문에 답할 채널이에요. 고른 채널의 모든 멤버가 GitHub 토큰이 읽을 수 있는 코드 내용을 보게 되니 좁게 고르세요.")
            if m.channels.isEmpty && !m.loadingChannels {
                if let e = m.channelsError { NoteView(note: CheckResult(ok: false, detail: "채널 목록을 못 불러왔어요: " + e, step: nil)) }
                LabeledField("채널 ID 직접 입력", placeholder: "C0123ABCD, C0456EFGH, dm", text: $m.allowedText,
                             caption: "쉼표로 구분하고, DM을 허용하려면 dm을 넣어요. 채널 ID는 슬랙에서 채널 이름 → 채널 세부정보 맨 아래에 있어요.")
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(m.channelChoices) { c in
                        Toggle(m.label(c), isOn: Binding(
                            get: { m.allowed.contains(c.id) },
                            set: { on in if on { m.allowed.insert(c.id) } else { m.allowed.remove(c.id) } }))
                            .toggleStyle(.checkbox)
                    }
                    Divider().padding(.vertical, 2)
                    Toggle("DM으로도 질문 받기", isOn: $m.allowDM).toggleStyle(.checkbox)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.secondary.opacity(0.08)))
                Text(m.allowed.isEmpty && !m.allowDM
                     ? "아무것도 고르지 않으면 봇이 초대된 모든 채널에서 답해요."
                     : "목록에는 봇이 들어간 채널만 보여요. 없는 채널은 슬랙에서 /invite @AskProject 후 다시 불러오세요.")
                    .font(.caption).foregroundStyle(m.allowed.isEmpty && !m.allowDM ? .orange : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            SectionHeader("운영 채널", "개발팀이 보는 채널 하나를 고르세요. 이 맥이 켜져 있다는 메시지 하나가 제자리에서 갱신되고, 다른 맥에서 같은 봇이 켜지면 알려 줘요.")
            Picker("운영 채널", selection: $m.hostChannel) {
                Text("없음").tag("")
                ForEach(m.channelChoices) { c in Text(m.label(c)).tag(c.id) }
            }
            .labelsHidden().frame(maxWidth: 360, alignment: .leading)

            SectionHeader("확인 요청 채널", "답변의 '개발자에게 확인 요청' 버튼이 이 채널로 보내요.")
            Picker("확인 요청 채널", selection: $m.escalation) {
                Text(m.hostChannel.isEmpty ? "운영 채널과 같게 (지금은 없음)" : "운영 채널과 같게").tag("")
                ForEach(m.channelChoices) { c in Text(m.label(c)).tag(c.id) }
            }
            .labelsHidden().frame(maxWidth: 360, alignment: .leading)
        } footer: {
            Button(m.loadingChannels ? "불러오는 중…" : "채널 다시 불러오기") { Task { await m.loadChannels() } }
                .disabled(m.loadingChannels)
            Spacer()
            Button("저장") {
                Task { await m.save(settings: ["allowed_channels": m.allowedText, "host_channel": m.hostChannel,
                                               "escalation_channel": m.escalation]) }
            }
            .buttonStyle(.borderedProminent).disabled(m.working)
        }
        .task { if m.channels.isEmpty { await m.loadChannels() } }
    }

    private var host: some View {
        let power = app.status?.power
        return SettingsPage {
            SectionHeader("절전", "맥이 잠들면 봇도 멈춰요.")
            Picker("절전", selection: Binding(get: { power?.mode ?? "lid-open" }, set: { app.setPower($0) })) {
                Text("기본 — 맥이 잠들면 봇도 멈춤").tag("default")
                Text("덮개 열림 — 깨어 있음(화면은 꺼져도 됨)").tag("lid-open")
                Text("덮개 닫힘 — 전원 연결 중이면 닫아도 깨어 있음").tag("lid-closed")
            }
            .pickerStyle(.radioGroup).labelsHidden()
            Text("덮개 닫힘 모드는 전원이 연결돼 있을 때만 동작해요. 가방에 넣으면 발열에 주의하세요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider().padding(.vertical, 4)
            SectionHeader("자동 로그인", nil)
            HStack {
                Text("현재")
                Text(power?.autologin_user.map { "켜짐 (\($0))" } ?? "꺼짐")
                    .foregroundStyle(power?.autologin_user == nil ? .orange : .primary)
            }
            if power?.filevault_on == true {
                Text("FileVault가 켜져 있으면 자동 로그인을 쓸 수 없어요. 재부팅 뒤 누군가 로그인할 때까지 봇이 멈춰요. 상시 운영 맥이라면 보안 정책과 함께 판단하세요.")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("재부팅(정전·업데이트) 뒤에도 봇이 다시 뜨려면 자동 로그인이 필요해요.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Button("사용자 및 그룹 설정 열기") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Users-Groups-Settings.extension")!)
                }
                Button("로그인 항목 열기") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension")!)
                }
            }
        }
    }

    private var domains: some View {
        SettingsPage {
            Text("도메인 맵과 용어 사전은 이번 버전에서 화면 편집을 지원하지 않아요. 기본값으로도 동작하고, 채우면 답의 정확도가 올라가요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let dir = app.paths?.config_dir {
                ForEach(["domains.yaml", "glossary.yaml"], id: \.self) { f in
                    GroupBox(f) {
                        ScrollView {
                            Text((try? String(contentsOfFile: dir + "/" + f, encoding: .utf8)) ?? "(없음)")
                                .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(height: 140)
                    }
                }
                Button("Finder에서 보기") { NSWorkspace.shared.open(URL(fileURLWithPath: dir)) }
            }
        }
    }

    private var about: some View {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "-"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "-"
        return SettingsPage {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                infoRow("앱", "\(version) (\(build))")
                infoRow("백엔드", "\(app.status?.version ?? "-") · 계약 v\(app.status?.protocol ?? 0)")
                infoRow("모드", app.isApp ? "배포 앱 (Keychain)" : "개발 (레포 .env)")
                if let p = app.paths {
                    infoRow("데이터", p.data_dir)
                    infoRow("로그", p.log_dir)
                }
            }
            HStack {
                Button("진단 묶음 만들기") { app.diagnostics() }
                if Updater.shared.available { Button("업데이트 확인") { Updater.shared.checkForUpdates() } }
                Button("제3자 고지") {
                    if let u = Bundle.main.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "txt") { NSWorkspace.shared.open(u) }
                }
            }
            .padding(.top, 6)
        }
    }

    private func infoRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// 설정 탭 공통 틀: 위에서부터 쌓고, 넘치면 스크롤, 저장 같은 동작은 아래 고정 줄에 둔다.
struct SettingsPage<Content: View, Footer: View>: View {
    @ViewBuilder var content: Content
    @ViewBuilder var footer: Footer

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
            }
            if Footer.self != EmptyView.self {
                Divider()
                HStack { footer }.padding(.horizontal, 20).padding(.vertical, 12)
            }
        }
    }
}

extension SettingsPage where Footer == EmptyView {
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
        self.footer = EmptyView()
    }
}

struct SectionHeader: View {
    let title: String
    let subtitle: String?

    init(_ title: String, _ subtitle: String?) {
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.headline)
            if let subtitle {
                Text(subtitle).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// 라벨을 필드 위에 두는 입력 칸(Form의 왼쪽 라벨 열을 쓰지 않는다).
struct LabeledField: View {
    let title: String
    let placeholder: String
    @Binding var text: String
    var caption: String? = nil

    init(_ title: String, placeholder: String, text: Binding<String>, caption: String? = nil) {
        self.title = title
        self.placeholder = placeholder
        self._text = text
        self.caption = caption
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            TextField(title, text: $text, prompt: Text(placeholder)).labelsHidden().textFieldStyle(.roundedBorder)
            if let caption {
                Text(caption).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

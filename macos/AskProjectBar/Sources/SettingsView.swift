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
    @Published var allowedText = ""
    @Published var hostChannel = ""
    @Published var escalation = ""

    func load() async {
        let (c, out) = await Backend.json(ConfigState.self, ["config", "get", "--json"])
        config = c
        if c == nil { note = CheckResult(ok: false, detail: "설정을 읽지 못했어요: " + out.errorLine, step: nil) }
        repos = c?.repos ?? []
        allowedText = c?.settings.allowed_channels ?? ""
        hostChannel = c?.settings.host_channel ?? ""
        escalation = c?.settings.escalation_channel ?? ""
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
        .frame(width: 640, height: 500)
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
        Form {
            ForEach(Self.secretRows, id: \.key) { row in
                let binding = Binding(get: { m.secrets[row.key] ?? "" }, set: { m.secrets[row.key] = $0 })
                TokenField(title: row.title, placeholder: row.prefix.isEmpty ? "" : row.prefix + "…", text: binding,
                           problem: row.prefix.isEmpty ? nil : WizardModel.prefixProblem(binding.wrappedValue, row.prefix, row.title),
                           saved: m.config?.secrets[row.key] ?? false)
            }
            if let t = app.status?.github_token, let d = t.days_left {
                Text("GitHub 토큰 만료까지 \(Int(d.rounded()))일").font(.caption).foregroundStyle(t.warning ? .orange : .secondary)
            }
            HStack {
                Spacer()
                Button("GitHub 새 토큰 만들기") {
                    Task {
                        let (u, _) = await Backend.json(URLResult.self, ["setup", "pat-url"])
                        if let u, let url = URL(string: u.url) { NSWorkspace.shared.open(url) }
                    }
                }
                Button("저장") {
                    let filled = m.secrets.filter { !$0.value.isEmpty }.mapValues { $0.trimmingCharacters(in: .whitespaces) }
                    Task { await m.save(secrets: filled) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(m.working || m.secrets.values.allSatisfy(\.isEmpty))
            }
        }
    }

    private var reposTab: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("봇이 읽는 레포(현재는 첫 번째 레포만 답에 쓰여요). 바꾸면 다음 동기화 때 새로 받아요.")
                .font(.caption).foregroundStyle(.secondary)
            ForEach($m.repos) { $r in
                HStack {
                    TextField("이름", text: $r.name).frame(width: 110)
                    TextField("https://github.com/조직/레포", text: $r.url)
                    TextField("브랜치", text: $r.ref).frame(width: 90)
                    Button(role: .destructive) { m.repos.removeAll { $0.id == r.id } } label: { Image(systemName: "minus.circle") }
                        .buttonStyle(.borderless)
                }
                TextField("한 줄 설명(프롬프트에 들어가요)", text: $r.description).font(.caption)
            }
            HStack {
                Button("레포 추가") { m.repos.append(RepoEntry(name: "repo\(m.repos.count + 1)", url: "", ref: "main", description: "")) }
                Spacer()
                Button("저장") {
                    let rows = m.repos.map { ["name": $0.name, "url": $0.url, "ref": $0.ref, "description": $0.description] }
                    Task { await m.save(repos: rows) }
                }
                .buttonStyle(.borderedProminent).disabled(m.working || m.repos.isEmpty)
            }
            Spacer()
        }
    }

    private var channels: some View {
        Form {
            TextField("답할 채널 ID (쉼표 구분, DM 허용은 dm)", text: $m.allowedText)
            Text("고른 채널의 모든 멤버가 GitHub 토큰이 읽을 수 있는 코드 내용을 보게 돼요. 비우면 봇이 초대된 모든 채널에서 답해요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField("운영 채널 ID (호스트 중복 감지)", text: $m.hostChannel)
            TextField("확인 요청 채널 ID ('개발자에게 확인 요청' 버튼)", text: $m.escalation)
            HStack {
                Button("마법사에서 목록으로 고르기") { WindowManager.shared.showWizard(step: .channels) }
                Spacer()
                Button("저장") {
                    Task { await m.save(settings: ["allowed_channels": m.allowedText, "host_channel": m.hostChannel,
                                                   "escalation_channel": m.escalation]) }
                }
                .buttonStyle(.borderedProminent).disabled(m.working)
            }
        }
    }

    private var host: some View {
        let power = app.status?.power
        return Form {
            Picker("절전", selection: Binding(get: { power?.mode ?? "lid-open" }, set: { app.setPower($0) })) {
                Text("기본 — 맥이 잠들면 봇도 멈춤").tag("default")
                Text("덮개 열림 — 깨어 있음(화면은 꺼져도 됨)").tag("lid-open")
                Text("덮개 닫힘 — 전원 연결 중이면 닫아도 깨어 있음").tag("lid-closed")
            }
            .pickerStyle(.radioGroup)
            Text("덮개 닫힘 모드는 전원이 연결돼 있을 때만 동작해요. 가방에 넣으면 발열에 주의하세요.")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            LabeledContent("자동 로그인") {
                Text(power?.autologin_user.map { "켜짐 (\($0))" } ?? "꺼짐").foregroundStyle(power?.autologin_user == nil ? .orange : .primary)
            }
            if power?.filevault_on == true {
                Text("FileVault가 켜져 있으면 자동 로그인을 쓸 수 없어요. 재부팅 뒤 누군가 로그인할 때까지 봇이 멈춰요. 상시 운영 맥이라면 보안 정책과 함께 판단하세요.")
                    .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            } else {
                Text("재부팅(정전·업데이트) 뒤에도 봇이 다시 뜨려면 자동 로그인이 필요해요.")
                    .font(.caption).foregroundStyle(.secondary)
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
        VStack(alignment: .leading, spacing: 8) {
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
        return Form {
            LabeledContent("앱", value: "\(version) (\(build))")
            LabeledContent("백엔드", value: "\(app.status?.version ?? "-") · 계약 v\(app.status?.protocol ?? 0)")
            LabeledContent("모드", value: app.isApp ? "배포 앱 (Keychain)" : "개발 (레포 .env)")
            if let p = app.paths {
                LabeledContent("데이터", value: p.data_dir).textSelection(.enabled)
                LabeledContent("로그", value: p.log_dir).textSelection(.enabled)
            }
            HStack {
                Button("진단 묶음 만들기") { app.diagnostics() }
                if Updater.shared.available { Button("업데이트 확인") { Updater.shared.checkForUpdates() } }
                Button("제3자 고지") {
                    if let u = Bundle.main.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "txt") { NSWorkspace.shared.open(u) }
                }
            }
        }
    }
}

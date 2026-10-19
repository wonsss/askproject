// 메뉴 막대 패널: 봇 상태·경고·오늘 질문·최근 질문·백업.

import AppKit
import SwiftUI

struct StatCard: View {
    let title: String
    let value: String
    var accent: Color = .primary
    var action: (() -> Void)?

    var body: some View {
        let content = VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.system(.title3, design: .rounded).weight(.semibold)).foregroundStyle(accent)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
        if let action {
            Button(action: action) { content }.buttonStyle(.plain)
        } else {
            content
        }
    }
}

struct JobRow: View {
    let name: String
    let job: JobState?
    var up: Bool? = nil  // 웹 콘솔만: 콘솔이 실제로 응답하는지(식별 헤더 확인)
    var portTaken = false  // 웹 콘솔만: 같은 포트에 다른 프로그램이 응답함

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(L(name)).font(.callout)
            Spacer()
            Text(text).font(.caption).foregroundStyle(.secondary)
        }
    }

    private var color: Color {
        if portTaken { return .orange }
        if let up { return up ? .green : (job?.state == "running" ? .orange : .gray) }
        switch job?.state {
        case "running": return .green
        case "waiting": return name == "백업" ? .blue : .orange
        default: return .gray
        }
    }

    private var text: String {
        if portTaken { return L("포트를 다른 프로그램이 사용 중") }
        if let up {
            if up { return L("실행 중") }
            return job?.state == "running" ? L("응답 없음") : L("꺼짐")
        }
        switch job?.state {
        case "running": return L("실행 중")
        case "waiting": return name == "백업" ? L("매일 03:30") : L("재시작 대기")
        default: return L("꺼짐")
        }
    }
}

struct WarningBanner: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text(text).font(.caption).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.orange.opacity(0.12)))
    }
}

struct PanelView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            Divider()
            if let s = model.status {
                if s.configured == false {
                    setupPrompt
                } else {
                    ForEach(model.warnings, id: \.self) { WarningBanner(text: $0) }
                    VStack(spacing: 4) {
                        JobRow(name: "봇", job: s.jobs["bot"])
                        JobRow(name: "웹 콘솔", job: s.jobs["console"], up: model.consoleUp, portTaken: model.consolePortTaken)
                        JobRow(name: "백업", job: s.jobs["backup"])
                        if let t = s.jobs["tunnel"], t.state != "off" { JobRow(name: "팀 공유", job: t) }
                    }
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                        StatCard(title: L("오늘 질문"), value: L("%ld건", s.today.questions))
                        StatCard(title: L("오늘 비용"), value: money(s.today.cost))
                        StatCard(title: L("이번 주 비용"), value: money(s.week.cost))
                        StatCard(title: L("확인 필요"), value: L("%ld건", s.check), accent: s.check > 0 ? .orange : .primary) {
                            model.openConsole("/check")
                        }
                    }
                    recent(s)
                    footer(s)
                }
            } else {
                Text(L("상태를 불러오는 중…")).foregroundStyle(.secondary)
            }
            if let m = model.message {
                Text(m).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Divider()
            HStack {
                Button(L("웹 콘솔")) { model.openConsole() }
                if let link = model.teamShareURL {
                    Button(L("팀 공유 링크")) { NSWorkspace.shared.open(link) }
                        .help(link.absoluteString)
                }
                Button(L("로그")) { model.openLogs() }
                Button(L("설정")) { WindowManager.shared.showSettings() }
                Spacer()
                Menu(L("더 보기")) {
                    Button(L("설정 마법사 다시 열기")) { WindowManager.shared.showWizard() }
                    Button(L("데이터와 개인정보")) { WindowManager.shared.showPrivacy() }
                    Button(L("백업에서 복구…")) { WindowManager.shared.showRestore() }
                    Button(L("진단 묶음 만들기…")) { model.diagnostics() }
                    if Updater.shared.available {
                        Button(L("업데이트 확인…")) { Updater.shared.checkForUpdates() }
                    }
                    Divider()
                    Button(L("AskProject 종료")) { NSApplication.shared.terminate(nil) }
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
            .controlSize(.small)
        }
        .padding(14)
        .frame(width: 360)
        .onAppear { model.refresh() }
    }

    private var setupPrompt: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("아직 설정 전이에요")).font(.headline)
            Text(L("슬랙 앱, GitHub 토큰, Claude API 키를 차례로 연결하면 봇이 켜져요. 10분쯤 걸려요."))
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button(L("설정 시작")) { WindowManager.shared.showWizard() }.buttonStyle(.borderedProminent)
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 8) {
            Image(systemName: model.health.symbol).foregroundStyle(model.health.color).font(.title2)
            VStack(alignment: .leading, spacing: 0) {
                Text("AskProject").font(.headline)
                Text(model.busy ?? L("봇 %@", model.health.label) + (model.isApp ? "" : L(" · 개발 모드")))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if model.busy != nil {
                ProgressView().controlSize(.small)
            }
            if model.status?.configured != false {
                Button(model.health == .off || model.health == .unknown ? L("켜기") : L("끄기")) { model.toggle() }
                    .buttonStyle(.borderedProminent)
                    .tint(model.health == .off ? .accentColor : .gray)
                    .disabled(model.busy != nil || model.protocolMismatch)
            }
        }
    }

    private func recent(_ s: AppStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("최근 질문")).font(.caption).foregroundStyle(.secondary)
            if s.recent.isEmpty {
                Text(L("아직 슬랙 질문이 없어요")).font(.callout).foregroundStyle(.secondary)
            }
            ForEach(s.recent.prefix(5)) { q in
                Button {
                    model.openConsole("/q/\(q.id)")
                } label: {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(q.question).font(.callout).lineLimit(1).truncationMode(.tail)
                        Text("\(timeAgo(q.ts)) · \(L(q.route)) · \(confidence(q.confidence)) · \(money(q.cost))"
                             + (q.flags.isEmpty ? "" : " · ⚠︎"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func footer(_ s: AppStatus) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "externaldrive").foregroundStyle(.secondary)
                Text(s.last_backup.map { L("백업 %ld시간 전", Int($0.age_hours.rounded())) + ($0.offsite ? "" : L(" · 맥 밖 사본 없음")) }
                     ?? L("백업 없음"))
                    .font(.caption)
                Spacer()
                Button {
                    model.openBackups()
                } label: {
                    Image(systemName: "folder")
                }
                .controlSize(.small).help(L("백업 폴더 열기"))
                Button(L("지금 백업")) { model.backupNow() }.controlSize(.small).disabled(model.busy != nil)
            }
            HStack(spacing: 6) {
                Image(systemName: s.provider == "subscription" ? "terminal" : "key").foregroundStyle(.secondary)
                if !model.isApp {
                    // 개발 PoC 전용: 구독 로그인(타인에게 서비스 불가). 배포 앱에는 없다
                    Picker("", selection: Binding(get: { s.provider ?? "api" }, set: { model.setProvider($0) })) {
                        Text("API").tag("api")
                        Text(L("구독")).tag("subscription")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 104)
                    .disabled(model.busy != nil)
                }
                Text(authText(s)).font(.caption).lineLimit(1).truncationMode(.tail)
                Spacer()
                Button(s.provider == "subscription" ? L("로그인 확인") : L("크레딧 확인")) { model.checkClaude() }
                    .controlSize(.small).disabled(model.busy != nil)
            }
        }
    }

    private func authText(_ s: AppStatus) -> String {
        if s.provider == "subscription" {
            if s.claude_cli == false { return L("claude CLI 없음") }
            guard let c = model.claude else { return L("로컬 구독 로그인") }
            return c.ok ? L("구독 로그인 정상") : L("로그인 필요 (claude auth login)")
        }
        if s.auth != "api_key" { return L("API 키 없음") }
        guard let c = model.claude else { return L("Claude API 키") }
        return c.ok ? L("API 키 · 크레딧 정상") : L("Claude 호출 불가")
    }
}

func money(_ v: Double) -> String { v < 1 ? String(format: "$%.4f", v) : String(format: "$%.2f", v) }

func confidence(_ c: String?) -> String {
    switch c { case "high": return "🟢"; case "medium": return "🟡"; case "low": return "🔴"; default: return "–" }
}

func timeAgo(_ ts: Double) -> String {
    let m = Int((Date().timeIntervalSince1970 - ts) / 60)
    if m < 1 { return L("방금") }
    if m < 60 { return L("%ld분 전", m) }
    if m < 1440 { return L("%ld시간 전", m / 60) }
    return L("%ld일 전", m / 1440)
}

// 설정 → 도메인·용어: '학습하기'(예상 비용) → 진행 → 미리 보기(추가·변경·삭제, 검증 경고, 근거) 항목별 체크 → 적용.
// 파일은 직접 쓰지 않는다. `askproject learn <kind> --dry-run|--json|--apply -`의 JSON만 부른다.
// 사람이 쓰거나 고친 항목은 백엔드가 잠그고(제안만), 적용 전 파일이 바뀌었으면 백엔드가 거부한다.

import AppKit
import SwiftUI

struct LearnMaterials: Decodable {
    let files: Int
    let routes: Int
    let apis: Int
    let docs: Int
    let questions: Int
}

struct LearnPlan: Decodable {
    let ok: Bool
    let ready: Bool?
    let reason: String?
    let model: String?
    let provider: String?
    let materials: LearnMaterials?
    let estimate_usd: Double?
    let max_usd: Double?
    let relearn_hint: String?
    let note: String?
    let error: String?
}

/// 항목 값: 도메인은 객체, 용어는 식별자 목록.
enum LearnValue: Decodable {
    case domain(name: String, summary: String, terms: [String], paths: [String])
    case identifiers([String])

    private struct D: Decodable {
        let name: String?
        let summary: String?
        let terms: [String]?
        let paths: [String]?
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let list = try? c.decode([String].self) {
            self = .identifiers(list)
        } else {
            let d = try c.decode(D.self)
            self = .domain(name: d.name ?? "", summary: d.summary ?? "", terms: d.terms ?? [], paths: d.paths ?? [])
        }
    }

    var title: String {
        switch self {
        case .domain(let name, _, _, _): return name
        case .identifiers: return ""  // 식별자는 detail 줄에만 보여 준다
        }
    }

    var detail: String {
        switch self {
        case .domain(_, let summary, let terms, let paths):
            return [summary, terms.isEmpty ? "" : "용어: " + terms.joined(separator: ", "),
                    "경로: " + paths.joined(separator: "  ")].filter { !$0.isEmpty }.joined(separator: "\n")
        case .identifiers(let ids): return "식별자: " + ids.joined(separator: ", ")
        }
    }
}

struct LearnItem: Decodable, Identifiable {
    let key: String
    let action: String   // add | change | remove | locked | same
    let before: LearnValue?
    let after: LearnValue?
    let evidence: [String]
    let warnings: [String]
    let locked_reason: String?
    let default_accept: Bool
    let file_count: Int?

    var id: String { key }
    var selectable: Bool { ["add", "change", "remove"].contains(action) }
}

struct LearnRejected: Decodable, Identifiable {
    let key: String
    let reason: String
    var id: String { key + reason }
}

struct LearnCoverageSide: Decodable {
    let files: Int
    let uncovered_ratio: Double
    let top_uncovered: [LearnDirCount]
}

struct LearnDirCount: Decodable, Identifiable {
    let dir: String
    let files: Int
    var id: String { dir }
}

struct LearnCoverage: Decodable {
    let before: LearnCoverageSide
    let after: LearnCoverageSide
}

struct LearnProposal: Decodable {
    let ok: Bool
    let id: String?
    let cost_usd: Double?
    let num_turns: Int?
    let items: [LearnItem]?
    let rejected: [LearnRejected]?
    let coverage: LearnCoverage?
    let agent_error: String?
    let error: String?
}

struct LearnApplyResult: Decodable {
    struct Skip: Decodable { let key: String; let reason: String }
    let ok: Bool
    let applied: [[String: String]]?
    let skipped: [Skip]?
    let backup: String?
    let restart_needed: Bool?
    let error: String?
}

@MainActor
final class LearnModel: ObservableObject {
    @Published var kind = "domains"
    @Published var plan: LearnPlan?
    @Published var proposal: LearnProposal?
    @Published var selected: Set<String> = []
    @Published var running = false
    @Published var startedAt: Date?
    @Published var applying = false
    @Published var note: CheckResult?
    @Published var fileVersion = 0  // 적용 뒤 YAML 보기를 다시 읽게 한다
    /// 마지막으로 화면을 준비한 종류. 탭을 옮겼다 돌아오면 .task가 다시 돌지만, 종류가 그대로면 학습 결과를 지우지 않는다
    var preparedKind: String?

    var label: String { kind == "domains" ? "도메인 맵" : "용어 사전" }

    func loadPlan() async {
        plan = nil
        let (p, out) = await Backend.json(LearnPlan.self, ["learn", kind, "--dry-run", "--json"])
        plan = p ?? LearnPlan(ok: false, ready: false, reason: nil, model: nil, provider: nil, materials: nil,
                              estimate_usd: nil, max_usd: nil, relearn_hint: nil, note: nil, error: out.errorLine)
    }

    func run() async {
        running = true
        startedAt = Date()
        note = nil
        proposal = nil
        defer { running = false; startedAt = nil }
        let (p, out) = await Backend.json(LearnProposal.self, ["learn", kind, "--json"])
        guard let p, p.ok else {
            note = CheckResult(ok: false, detail: "학습하지 못했어요: " + (p?.error ?? out.errorLine), step: nil)
            return
        }
        proposal = p
        selected = Set((p.items ?? []).filter { $0.selectable && $0.default_accept }.map(\.key))
        if (p.items ?? []).allSatisfy({ !$0.selectable }) {
            note = CheckResult(ok: true, detail: "바꿀 항목이 없어요. 지금 \(label)와 같거나 잠긴 항목뿐이에요.", step: nil)
        }
    }

    func apply(onApplied: () -> Void) async {
        guard let pid = proposal?.id else { return }
        applying = true
        defer { applying = false }
        let body: [String: Any] = ["proposal_id": pid, "accept": Array(selected)]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        let (r, out) = await Backend.json(LearnApplyResult.self, ["learn", kind, "--apply", "-", "--json"], input: data)
        guard let r, r.ok else {
            note = CheckResult(ok: false, detail: "적용하지 못했어요: " + (r?.error ?? out.errorLine), step: nil)
            return
        }
        let skipped = (r.skipped ?? []).map { "\($0.key): \($0.reason)" }
        note = CheckResult(ok: true, detail: "\(r.applied?.count ?? 0)개 항목을 적용했어요."
                           + (r.backup.map { " 이전 파일은 백업했어요(\(($0 as NSString).lastPathComponent))." } ?? "")
                           + (skipped.isEmpty ? "" : "\n건너뜀 — " + skipped.joined(separator: " · ")), step: nil)
        proposal = nil
        selected = []
        fileVersion += 1
        if r.restart_needed == true { onApplied() }
        await loadPlan()
    }
}

struct DomainsLearnView: View {
    let configDir: String?
    @ObservedObject var app = AppModel.shared
    @StateObject private var m = LearnModel()
    @State private var confirming = false

    var body: some View {
        SettingsPage {
            Text("AI가 레포의 화면·API·문서를 읽고 초안을 써요. 바로 저장하지 않고, 검증 결과를 보고 고른 항목만 파일에 반영해요. "
                 + "직접 쓰거나 고친 항목은 잠겨서 학습이 바꾸지 않아요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("", selection: $m.kind) {
                Text("도메인 맵").tag("domains")
                Text("용어 사전").tag("glossary")
            }
            .pickerStyle(.segmented).labelsHidden().disabled(m.running || m.applying)

            learnBox
            if let n = m.note { NoteView(note: n) }
            if let p = m.proposal { preview(p) }
            currentFile
        } footer: {
            if m.proposal != nil {
                Button("버리기") { m.proposal = nil; m.selected = [] }.disabled(m.applying)
                Spacer()
                if m.applying { ProgressView().controlSize(.small) }
                Button("선택한 \(m.selected.count)개 적용") {
                    Task { await m.apply { app.restartBotIfRunning() } }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(m.selected.isEmpty || m.applying)
            }
        }
        .task(id: m.kind) {
            // 설정 창의 다른 탭에 갔다 오면 이 작업이 다시 돈다. 그때 방금 받은 제안(1분·약 $0.8)을 버리지 않게,
            // 도메인 맵 ↔ 용어 사전을 바꿀 때만 비운다
            if m.preparedKind != m.kind {
                m.proposal = nil
                m.selected = []
                m.note = nil
                m.preparedKind = m.kind
            }
            await m.loadPlan()
        }
    }

    private func money(_ v: Double?) -> String { v.map { String(format: "$%.2f", $0) } ?? "-" }

    private var learnBox: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                if let p = m.plan {
                    if let err = p.error {
                        Text("예상 비용을 계산하지 못했어요: " + err).font(.caption).foregroundStyle(.orange)
                    } else {
                        Text("예상 약 \(money(p.estimate_usd)) (1회 상한 \(money(p.max_usd))) · \(p.model ?? "")")
                            .font(.callout)
                        if let mt = p.materials {
                            Text("재료: 파일 \(mt.files) · 화면 \(mt.routes) · API \(mt.apis) · 문서 \(mt.docs) · 최근 질문 \(mt.questions)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let n = p.note { Text(n).font(.caption).foregroundStyle(.secondary) }
                        if let hint = p.relearn_hint {
                            Label(hint, systemImage: "sparkles").font(.caption).foregroundStyle(.blue)
                        }
                        if p.ready == false, let r = p.reason {
                            Text(r).font(.caption).foregroundStyle(.orange)
                        }
                    }
                } else {
                    ProgressView().controlSize(.small)
                }
                HStack {
                    Button(m.running ? "학습 중…" : "학습하기…") { confirming = true }
                        .disabled(m.running || m.applying || m.plan?.ready != true)
                    if m.running, let start = m.startedAt {
                        ProgressView().controlSize(.small)
                        TimelineView(.periodic(from: start, by: 1)) { ctx in
                            Text("\(Int(ctx.date.timeIntervalSince(start)))초 · 보통 1~3분 걸려요")
                                .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Text("\(m.label) 학습하기") }
        .confirmationDialog("\(m.label)를 학습할까요?", isPresented: $confirming) {
            Button("학습하기 (약 \(money(m.plan?.estimate_usd)))") { Task { await m.run() } }
            Button("취소", role: .cancel) {}
        } message: {
            Text("Claude API를 호출해요. 예상 약 \(money(m.plan?.estimate_usd))이고, 대화가 길어지면 더 들어요. api 모드에서는 약 \(money(m.plan?.max_usd))에 이르면 멈춰요. "
                 + "결과는 미리 보기로만 보여 주고, 적용하기 전에는 파일을 바꾸지 않아요.")
        }
    }

    @ViewBuilder
    private func preview(_ p: LearnProposal) -> some View {
        let items = (p.items ?? []).filter { $0.action != "same" }
        GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                Text("비용 \(money(p.cost_usd)) · 대화 \(p.num_turns ?? 0)회"
                     + (p.agent_error.map { " · 경고: \($0)" } ?? ""))
                    .font(.caption).foregroundStyle(.secondary)
                if let cov = p.coverage {
                    Text("어느 도메인에도 안 걸리는 파일: \(pct(cov.before.uncovered_ratio)) → \(pct(cov.after.uncovered_ratio)) (모두 적용 시)")
                        .font(.caption)
                    if !cov.after.top_uncovered.isEmpty {
                        Text("남는 폴더: " + cov.after.top_uncovered.map { "\($0.dir) \($0.files)" }.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if items.isEmpty { Text("바꿀 항목이 없어요.").font(.callout) }
                ForEach(items) { item in
                    row(item)
                    Divider()
                }
                if let rej = p.rejected, !rej.isEmpty {
                    DisclosureGroup("검증에서 버린 항목 \(rej.count)개") {
                        ForEach(rej) { r in
                            Text("✗ \(r.key) — \(r.reason)").font(.caption).foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .font(.caption)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } label: { Text("미리 보기 — 적용할 항목을 고르세요") }
    }

    private func pct(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    private func badge(_ action: String) -> (String, Color) {
        switch action {
        case "add": return ("추가", .green)
        case "change": return ("변경", .blue)
        case "remove": return ("삭제", .red)
        default: return ("잠김", .secondary)
        }
    }

    private func row(_ item: LearnItem) -> some View {
        let (text, color) = badge(item.action)
        let binding = Binding(
            get: { m.selected.contains(item.key) },
            set: { on in if on { m.selected.insert(item.key) } else { m.selected.remove(item.key) } })
        return HStack(alignment: .top, spacing: 8) {
            Toggle("", isOn: binding).labelsHidden().disabled(!item.selectable)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(text).font(.caption2.bold()).padding(.horizontal, 5).padding(.vertical, 1)
                        .background(RoundedRectangle(cornerRadius: 4).fill(color.opacity(0.15))).foregroundStyle(color)
                    Text(item.key).font(.system(.callout, design: .monospaced))
                    Text((item.after ?? item.before)?.title ?? "").font(.callout.bold())
                    if let n = item.file_count { Text("파일 \(n)개").font(.caption).foregroundStyle(.secondary) }
                }
                if item.action == "change" || item.action == "locked", let before = item.before {
                    Text("지금: " + before.detail).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let v = item.after ?? item.before {
                    Text((item.action == "change" || item.action == "locked" ? "제안: " : "") + v.detail)
                        .font(.caption).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                }
                if let r = item.locked_reason {
                    Label("\(r)이라 바꾸지 않아요. 원하면 파일에서 직접 고치세요.", systemImage: "lock.fill")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !item.evidence.isEmpty {
                    Text("근거: " + item.evidence.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(item.warnings, id: \.self) { w in
                    Label(w, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private var currentFile: some View {
        if let dir = configDir {
            let f = m.kind == "domains" ? "domains.yaml" : "glossary.yaml"
            DisclosureGroup("지금 \(f) 보기") {
                ScrollView {
                    Text((try? String(contentsOfFile: dir + "/" + f, encoding: .utf8)) ?? "(없음)")
                        .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 160)
                .id(m.fileVersion)
            }
            Button("Finder에서 보기") { NSWorkspace.shared.open(URL(fileURLWithPath: dir)) }
        }
    }
}

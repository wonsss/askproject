// AskProject 앱 아이콘 생성: 둥근 사각형 바탕 + 말풍선 + 코드 괄호 속 물음표.
//   bash scripts/make-icon.sh   # → macos/AskProjectBar/AppIcon.icns
// 외부 의존 없이 AppKit으로 그린다. 디자인을 바꾸면 이 파일을 고치고 다시 실행해 icns를 커밋한다.
import AppKit

let args = CommandLine.arguments
guard args.count == 2 else {
    FileHandle.standardError.write("사용법: make-icon.swift <iconset 디렉터리>\n".data(using: .utf8)!)
    exit(2)
}
let outDir = URL(fileURLWithPath: args[1])
try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: r / 255, green: g / 255, blue: b / 255, alpha: a)
}

/// 1024 기준 좌표로 그린다(원점 왼쪽 아래).
func draw(in ctx: CGContext) {
    // macOS 아이콘 격자: 1024 캔버스 안 824 정사각형, 모서리 반경 약 185
    let plate = CGRect(x: 100, y: 100, width: 824, height: 824)
    let platePath = CGPath(roundedRect: plate, cornerWidth: 185, cornerHeight: 185, transform: nil)

    // 그림자
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -12), blur: 28, color: rgb(0, 0, 0, 0.35).cgColor)
    ctx.addPath(platePath)
    ctx.setFillColor(rgb(40, 50, 120).cgColor)
    ctx.fillPath()
    ctx.restoreGState()

    // 바탕 그라데이션(위 보라 → 아래 파랑)
    ctx.saveGState()
    ctx.addPath(platePath)
    ctx.clip()
    let bg = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB),
                        colors: [rgb(124, 92, 255).cgColor, rgb(52, 120, 246).cgColor] as CFArray,
                        locations: [0, 1])!
    ctx.drawLinearGradient(bg, start: CGPoint(x: 300, y: 924), end: CGPoint(x: 724, y: 100),
                           options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
    // 위쪽 은은한 하이라이트
    let shine = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB),
                           colors: [rgb(255, 255, 255, 0.18).cgColor, rgb(255, 255, 255, 0).cgColor] as CFArray,
                           locations: [0, 1])!
    ctx.drawLinearGradient(shine, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 560), options: [])
    ctx.restoreGState()

    // 말풍선(왼쪽 아래 꼬리)
    let bubble = CGRect(x: 230, y: 330, width: 564, height: 430)
    let bubblePath = CGMutablePath()
    bubblePath.addRoundedRect(in: bubble, cornerWidth: 120, cornerHeight: 120)
    bubblePath.move(to: CGPoint(x: 300, y: 360))
    bubblePath.addLine(to: CGPoint(x: 250, y: 230))
    bubblePath.addLine(to: CGPoint(x: 420, y: 340))
    bubblePath.closeSubpath()
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -8), blur: 20, color: rgb(20, 20, 80, 0.30).cgColor)
    ctx.addPath(bubblePath)
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fillPath()
    ctx.restoreGState()

    // 말풍선 안: ‹ ? › — 코드에 묻는다
    let ink = rgb(84, 104, 250)
    ctx.setStrokeColor(ink.cgColor)
    ctx.setLineWidth(46)
    ctx.setLineCap(.round)
    ctx.setLineJoin(.round)
    let midY = bubble.midY
    ctx.move(to: CGPoint(x: 400, y: midY + 95))
    ctx.addLine(to: CGPoint(x: 315, y: midY))
    ctx.addLine(to: CGPoint(x: 400, y: midY - 95))
    ctx.move(to: CGPoint(x: 624, y: midY + 95))
    ctx.addLine(to: CGPoint(x: 709, y: midY))
    ctx.addLine(to: CGPoint(x: 624, y: midY - 95))
    ctx.strokePath()

    var font = NSFont.systemFont(ofSize: 300, weight: .heavy)
    if let rounded = font.fontDescriptor.withDesign(.rounded), let f = NSFont(descriptor: rounded, size: 300) {
        font = f
    }
    let q = NSAttributedString(string: "?", attributes: [.font: font, .foregroundColor: ink])
    let size = q.size()
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
    q.draw(at: CGPoint(x: 512 - size.width / 2, y: midY - size.height / 2 + 8))
    NSGraphicsContext.restoreGraphicsState()
}

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let ctx = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    ctx.scaleBy(x: CGFloat(px) / 1024, y: CGFloat(px) / 1024)
    draw(in: ctx)
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try render(base).write(to: outDir.appendingPathComponent("icon_\(base)x\(base).png"))
    try render(base * 2).write(to: outDir.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}

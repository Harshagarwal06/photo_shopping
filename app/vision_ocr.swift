import CoreImage
import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("Expected an image path.\n", stderr)
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard
    let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    fputs("The uploaded image could not be decoded.\n", stderr)
    exit(3)
}

// A phone stores a photo's rotation in an EXIF tag instead of in the pixels, and
// CGImageSourceCreateImageAtIndex returns those unrotated pixels. Without the tag
// Vision reads a sideways photo sideways.
let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
let orientation = (properties?[kCGImagePropertyOrientation] as? UInt32)
    .flatMap(CGImagePropertyOrientation.init) ?? .up

// One scale cannot read every hand. Enlarging separates strokes that merge at
// native resolution — the "bl" of "black" is otherwise recognised as "H" — but
// enlarging too far loses whole lines of cursive. Measured across three
// photographed lists, each scale recovered lines the others missed, so all three
// are read and the results merged: 17 of 31 expected terms at 2x alone, 23 when
// merged.
let recognitionScales: [CGFloat] = [1, 2, 4]
let ciContext = CIContext()

struct Reading {
    let y: Double
    let x: Double
    let width: Double
    let height: Double
    let scale: CGFloat
    let candidates: [VNRecognizedText]
}

func readings(scale: CGFloat) -> [Reading] {
    // Rotate the pixels rather than handing the tag to Vision: the bounding boxes
    // are then already in upright space, so the reading-order sort below stays
    // correct. Passing the tag to the handler instead reverses the lines.
    let prepared = CIImage(cgImage: image)
        .oriented(orientation)
        .transformed(by: CGAffineTransform(scaleX: scale, y: scale))
    guard let rendered = ciContext.createCGImage(prepared, from: prepared.extent) else {
        return []
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    // Vision's language model is general-purpose. Supplying common grocery
    // categories, quantities, and Indian retail brands gives its alternate
    // candidates a useful domain prior without forcing any one answer.
    request.customWords = [
        "Amul", "Aashirvaad", "Britannia", "Cadbury", "Daawat", "Fortune",
        "Kellogg's", "KitKat", "Knorr", "Kurkure", "Maggi", "Mother Dairy",
        "Oreo", "Pintola", "Real", "Rin", "Tata",
        "atta", "basmati", "besan", "bhindi", "bread", "butter", "cheese",
        "chicken breast", "chilli", "cocoa powder", "coffee", "cornflakes",
        "curd", "dal", "detergent bar", "eggs", "flour", "fruit juice",
        "ghee", "ice cream sandwich", "juice", "litres", "loaf", "masala",
        "milk", "mixed fruit", "oil", "oregano", "paneer", "peanut butter",
        "pencil box", "Puffcorn", "rice", "salt", "soap", "soup powder",
        "sugar", "tea", "tomato", "tomato soup powder", "turmeric", "water"
    ]
    guard (try? VNImageRequestHandler(cgImage: rendered, options: [:]).perform([request])) != nil
    else {
        return []
    }
    return (request.results ?? []).compactMap { observation in
        let candidates = observation.topCandidates(5)
        return candidates.isEmpty
            ? nil
            : Reading(
                y: Double(observation.boundingBox.midY),
                x: Double(observation.boundingBox.minX),
                width: Double(observation.boundingBox.width),
                height: Double(observation.boundingBox.height),
                scale: scale,
                candidates: candidates
            )
    }
}

struct CandidateOutput: Codable {
    let confidence: Float
    let text: String
}

struct LineOutput: Codable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let candidates: [CandidateOutput]
}

func verticalOverlap(_ first: Reading, _ second: Reading) -> Double {
    let firstMin = first.y - first.height / 2
    let firstMax = first.y + first.height / 2
    let secondMin = second.y - second.height / 2
    let secondMax = second.y + second.height / 2
    let overlap = max(0, min(firstMax, secondMax) - max(firstMin, secondMin))
    return overlap / max(0.0001, min(first.height, second.height))
}

func horizontalGap(_ first: Reading, _ second: Reading) -> Double {
    let firstMax = first.x + first.width
    let secondMax = second.x + second.width
    if firstMax < second.x {
        return second.x - firstMax
    }
    if secondMax < first.x {
        return first.x - secondMax
    }
    return 0
}

func horizontalOverlapRatio(_ first: Reading, _ second: Reading) -> Double {
    let overlap = max(
        0,
        min(first.x + first.width, second.x + second.width) - max(first.x, second.x)
    )
    return overlap / max(0.0001, min(first.width, second.width))
}

func belongsToLine(_ reading: Reading, _ group: [Reading]) -> Bool {
    group.contains { existing in
        // Adjacent notebook rows can be only ~0.019 apart. A looser vertical
        // tolerance is safe only for left-to-right fragments that do not occupy
        // the same horizontal space; overlapping fragments at that distance
        // belong to adjacent rows.
        let delta = abs(reading.y - existing.y)
        let gap = horizontalGap(reading, existing)
        if delta <= 0.013 {
            return gap <= 0.12
        }
        return delta <= 0.022
            && gap <= 0.04
            && horizontalOverlapRatio(reading, existing) < 0.4
    }
}

func output(for group: [Reading]) -> LineOutput? {
    guard !group.isEmpty else { return nil }
    var alternatives: [CandidateOutput] = []
    var seen = Set<String>()

    // OCR sometimes emits a whole line at one scale and several word fragments
    // at another. Reconstruct each scale independently, left to right, and keep
    // several ranked alternatives instead of discarding everything but top-1.
    let scales = Dictionary(grouping: group, by: { $0.scale })
    for scale in recognitionScales {
        guard let observations = scales[scale] else { continue }
        let ordered = observations.sorted(by: { $0.x < $1.x })
        let maximumRank = ordered.map(\.candidates.count).max() ?? 0
        for rank in 0..<min(5, maximumRank) {
            let chosen = ordered.compactMap { observation -> VNRecognizedText? in
                guard !observation.candidates.isEmpty else { return nil }
                return observation.candidates[min(rank, observation.candidates.count - 1)]
            }
            let text = chosen.map(\.string).joined(separator: " ")
                .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let key = text.folding(options: [.caseInsensitive, .diacriticInsensitive],
                                   locale: Locale(identifier: "en_US"))
            guard !text.isEmpty, !seen.contains(key) else { continue }
            seen.insert(key)
            let confidence = chosen.isEmpty
                ? 0
                : chosen.map(\.confidence).reduce(0, +) / Float(chosen.count)
            alternatives.append(CandidateOutput(confidence: confidence, text: text))
        }
    }

    alternatives.sort { first, second in
        if first.confidence != second.confidence {
            return first.confidence > second.confidence
        }
        if first.text.count != second.text.count {
            return first.text.count > second.text.count
        }
        return first.text.localizedStandardCompare(second.text) == .orderedAscending
    }
    let minX = group.map(\.x).min() ?? 0
    let maxX = group.map({ $0.x + $0.width }).max() ?? minX
    let minY = group.map({ $0.y - $0.height / 2 }).min() ?? 0
    let maxY = group.map({ $0.y + $0.height / 2 }).max() ?? minY
    return LineOutput(
        x: minX,
        y: (minY + maxY) / 2,
        width: maxX - minX,
        height: maxY - minY,
        candidates: Array(alternatives.prefix(12))
    )
}

let allReadings = recognitionScales.flatMap(readings(scale:))
if allReadings.isEmpty {
    fputs("Vision could not read any text in the image.\n", stderr)
    exit(4)
}

var lines: [[Reading]] = []
for reading in allReadings.sorted(by: { $0.y > $1.y }) {
    if let existing = lines.firstIndex(where: { belongsToLine(reading, $0) }) {
        lines[existing].append(reading)
    } else {
        lines.append([reading])
    }
}

let ordered = lines.compactMap(output(for:)).sorted { first, second in
    if abs(first.y - second.y) > 0.01 {
        return first.y > second.y
    }
    return first.x < second.x
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
for line in ordered {
    if let data = try? encoder.encode(line),
       let json = String(data: data, encoding: .utf8) {
        print(json)
    }
}

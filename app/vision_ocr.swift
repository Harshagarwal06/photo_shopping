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
    let text: String
    let confidence: Float
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
    guard (try? VNImageRequestHandler(cgImage: rendered, options: [:]).perform([request])) != nil
    else {
        return []
    }
    return (request.results ?? []).compactMap { observation in
        observation.topCandidates(1).first.map { candidate in
            Reading(
                y: Double(observation.boundingBox.midY),
                x: Double(observation.boundingBox.minX),
                text: candidate.string,
                confidence: candidate.confidence
            )
        }
    }
}

let allReadings = recognitionScales.flatMap(readings(scale:))
if allReadings.isEmpty {
    fputs("Vision could not read any text in the image.\n", stderr)
    exit(4)
}

// Group the same physical line of writing across the scales. Position must match
// horizontally as well as vertically, so two separate runs of text side by side
// stay two lines instead of one silently swallowing the other.
let lineTolerance = 0.012
let columnTolerance = 0.06
var lines: [[Reading]] = []
for reading in allReadings.sorted(by: { $0.y > $1.y }) {
    let existing = lines.firstIndex { group in
        guard let first = group.first else { return false }
        return abs(first.y - reading.y) < lineTolerance
            && abs(first.x - reading.x) < columnTolerance
    }
    if let existing {
        lines[existing].append(reading)
    } else {
        lines.append([reading])
    }
}

let ordered = lines.sorted { first, second in
    guard let a = first.first, let b = second.first else { return false }
    // The same tolerance the grouping uses: a coarser one here lets two adjacent
    // lines fall into the left-to-right tiebreak and swap places.
    if abs(a.y - b.y) > lineTolerance {
        return a.y > b.y
    }
    return a.x < b.x
}

for line in ordered {
    // Vision's own confidence picks the better reading more often than preferring
    // the longest string does: 23 of 31 expected terms against 20.
    let best = line.max { first, second in
        (first.confidence, first.text.count) < (second.confidence, second.text.count)
    }
    if let best {
        // Confidence first, tab separated: the caller drops readings too poor to
        // shop from, and says how many it dropped.
        print("\(best.confidence)\t\(best.text)")
    }
}

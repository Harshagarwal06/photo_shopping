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

// Handwritten strokes that nearly touch merge into one glyph at phone-photo
// resolution: the "bl" of "black" is recognised as "H". Doubling the pixels keeps
// the gap. 2 is deliberate — 4 regressed on the same photo, and raising contrast
// fixed that word while corrupting another.
let recognitionScale: CGFloat = 2
// Rotate the pixels rather than handing the tag to Vision: the observation
// bounding boxes below are then already in upright space, so the reading-order
// sort stays correct. Passing the tag to the handler instead leaves the boxes in
// the stored orientation and reverses the recognised lines.
let prepared = CIImage(cgImage: image)
    .oriented(orientation)
    .transformed(by: CGAffineTransform(scaleX: recognitionScale, y: recognitionScale))
guard let upscaled = CIContext().createCGImage(prepared, from: prepared.extent) else {
    fputs("The uploaded image could not be prepared for recognition.\n", stderr)
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["en-US"]

let handler = VNImageRequestHandler(cgImage: upscaled, options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("Vision could not process the image: \(error)\n", stderr)
    exit(4)
}

let observations = (request.results ?? []).sorted {
    let verticalDistance = abs($0.boundingBox.midY - $1.boundingBox.midY)
    if verticalDistance > 0.02 {
        return $0.boundingBox.midY > $1.boundingBox.midY
    }
    return $0.boundingBox.minX < $1.boundingBox.minX
}

for observation in observations {
    if let candidate = observation.topCandidates(1).first {
        print(candidate.string)
    }
}

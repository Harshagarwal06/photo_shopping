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

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["en-US"]

let handler = VNImageRequestHandler(cgImage: image, options: [:])
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

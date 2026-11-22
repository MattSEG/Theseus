import Foundation
import ImageIO
import Vision

// Input is the normalized screenshot. Emit only bounds for the requested label.
func normalized(_ value: String) -> String {
    value.precomposedStringWithCanonicalMapping
        .split(whereSeparator: { $0.isWhitespace }).joined(separator: " ").lowercased()
}

let data = FileHandle.standardInput.readDataToEndOfFile()
guard [2, 4].contains(CommandLine.arguments.count),
      let source = CGImageSourceCreateWithData(data as CFData, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }
let labels = CommandLine.arguments.dropFirst().map(normalized)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
try VNImageRequestHandler(cgImage: image).perform([request])
let width = Double(image.width), height = Double(image.height)
var grouped = Array(repeating: [[Int]](), count: labels.count)
for observation in request.results ?? [] {
    guard let value = observation.topCandidates(1).first else { continue }
    let text = normalized(value.string)
    if !labels.contains(text) { continue }
    let b = observation.boundingBox
    let box = [max(0, Int(floor(b.minX * width))), max(0, Int(floor((1 - b.maxY) * height))),
                  min(image.width, Int(ceil(b.maxX * width))), min(image.height, Int(ceil((1 - b.minY) * height)))]
    for index in labels.indices where labels[index] == text { grouped[index].append(box) }
}
for i in grouped.indices { grouped[i].sort { ($0[1], $0[0]) < ($1[1], $1[0]) } }
var output: [String: Any] = ["boxes": grouped[0], "recognizer_revision": request.revision]
if labels.count == 3 { output["boundary_boxes"] = Array(grouped.dropFirst()) }
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output))

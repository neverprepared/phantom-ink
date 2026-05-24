---
name: process-diagrams
description: Create business process and workflow diagrams. Covers BPMN (formal ISO standard for business processes) and blockdiag (simple block/component overviews). Obsidian-kroki rendering.
---

# Process Diagrams

Model business processes, workflows, and component flows.

**Recommended:** BPMN for formal business process documentation (standard, tool-interoperable). blockdiag for simple block-level system or workflow overviews.

## In Obsidian (obsidian-kroki)

Use a fenced code block with the type as language identifier — renders inline automatically.

## BPMN — `bpmn` (companion required)

Best for: formal business process documentation following the ISO 19510 BPMN 2.0 standard. Understood by business analysts and integrates with process execution engines.

BPMN source is XML. The `<BPMNDiagram>` section controls visual layout — without it Kroki won't render.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
             xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
             xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
             targetNamespace="http://example.com">

  <process id="OrderProcess" isExecutable="false">
    <startEvent id="Start" name="Order Received"/>
    <sequenceFlow id="f1" sourceRef="Start" targetRef="Validate"/>

    <userTask id="Validate" name="Validate Order"/>
    <sequenceFlow id="f2" sourceRef="Validate" targetRef="Gateway"/>

    <exclusiveGateway id="Gateway" name="Valid?" default="f_no"/>
    <sequenceFlow id="f_yes" sourceRef="Gateway" targetRef="Charge" name="Yes">
      <conditionExpression>${valid}</conditionExpression>
    </sequenceFlow>
    <sequenceFlow id="f_no" sourceRef="Gateway" targetRef="Reject" name="No"/>

    <serviceTask id="Charge" name="Process Payment"/>
    <sequenceFlow id="f3" sourceRef="Charge" targetRef="Done"/>

    <endEvent id="Done"   name="Order Complete"/>
    <endEvent id="Reject" name="Order Rejected"/>
  </process>

  <bpmndi:BPMNDiagram>
    <bpmndi:BPMNPlane bpmnElement="OrderProcess">
      <bpmndi:BPMNShape bpmnElement="Start"   id="s1"><dc:Bounds x="150" y="82"  width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="Validate" id="s2"><dc:Bounds x="240" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="Gateway" id="s3"><dc:Bounds x="395" y="75"  width="50"  height="50"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="Charge"  id="s4"><dc:Bounds x="500" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="Done"    id="s5"><dc:Bounds x="660" y="82"  width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="Reject"  id="s6"><dc:Bounds x="397" y="200" width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNEdge bpmnElement="f1" id="e1"><di:waypoint x="186" y="100"/><di:waypoint x="240" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f2" id="e2"><di:waypoint x="340" y="100"/><di:waypoint x="395" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_yes" id="e3"><di:waypoint x="445" y="100"/><di:waypoint x="500" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_no" id="e4"><di:waypoint x="420" y="125"/><di:waypoint x="420" y="218"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f3" id="e5"><di:waypoint x="600" y="100"/><di:waypoint x="660" y="100"/></bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</definitions>
```

Key element types:
- Events: `startEvent`, `endEvent`, `intermediateCatchEvent`, `intermediateThrowEvent`
- Tasks: `task`, `userTask`, `serviceTask`, `scriptTask`, `sendTask`, `receiveTask`
- Gateways: `exclusiveGateway` (XOR) · `parallelGateway` (AND) · `inclusiveGateway` (OR) · `eventBasedGateway`
- Containers: `subProcess`, pools/lanes via `<collaboration>` + `<participant>` + `<laneSet>`

**Tip:** For complex BPMN, draw in [bpmn.io](https://bpmn.io) or Camunda Modeler, export the XML, then render with Kroki. Make sure to export uncompressed XML (not base64-encoded).

## blockdiag — `blockdiag` (companion required)

Best for: simple block-level system overviews and workflow step diagrams. Much simpler than BPMN.

```
blockdiag {
  orientation = landscape;

  Gateway  [label = "API Gateway",    color = "lightblue"];
  Auth     [label = "Auth Service"];
  Orders   [label = "Order Service"];
  Products [label = "Product Service"];
  Queue    [label = "Message Queue",  color = "lightgreen", shape = "roundedBox"];
  Notify   [label = "Notification",   color = "lightyellow"];

  group services {
    label = "Core Services"; color = "#eef";
    Auth; Orders; Products;
  }

  Gateway -> Auth    [label = "verify"];
  Gateway -> Orders  [label = "POST /orders"];
  Gateway -> Products[label = "GET /products"];
  Orders  -> Queue   [label = "publish"];
  Queue   -> Notify  [label = "consume"];
}
```

Node attributes: `label` · `color` · `shape` (box, roundedBox, diamond, ellipse, note, cloud, actor) · `style` (solid, dashed)  
Edge attributes: `label` · `color` · `style`  
Groups: `group name { label = "..."; color = "..."; Node1; Node2; }`

## Choosing

| Need | Tool |
|------|------|
| Formal business process (ISO BPMN, executable, tool-interoperable) | BPMN |
| Simple block-level workflow or system overview | blockdiag |

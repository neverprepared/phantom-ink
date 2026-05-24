---
name: bpmn
description: BPMN (Business Process Model and Notation) diagram syntax — standard XML-based notation for business process flows with events, tasks, gateways, pools, and lanes. Renders via phantom-diagrams MCP (requires companion).
---

# BPMN — Business Process Model and Notation

BPMN is the international standard (ISO 19510) for modeling business processes. Diagrams describe who does what, in what order, and under what conditions.

## Rendering

```
convert_diagram("bpmn", source, "svg")
```

SVG only. **Requires companion container.**

## Source Format

BPMN source is XML following the BPMN 2.0 schema. Every diagram is a `<definitions>` document containing `<process>` elements and a `<BPMNDiagram>` for layout.

## Minimal Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
             xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
             xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
             targetNamespace="http://example.com/bpmn">

  <process id="Process_1" isExecutable="false">
    <startEvent id="Start" name="Order Received"/>
    <sequenceFlow id="f1" sourceRef="Start" targetRef="Task1"/>
    <task id="Task1" name="Validate Order"/>
    <sequenceFlow id="f2" sourceRef="Task1" targetRef="End"/>
    <endEvent id="End" name="Order Confirmed"/>
  </process>

  <bpmndi:BPMNDiagram>
    <bpmndi:BPMNPlane bpmnElement="Process_1">
      <bpmndi:BPMNShape id="Start_di" bpmnElement="Start">
        <dc:Bounds x="150" y="80" width="36" height="36"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task1_di" bpmnElement="Task1">
        <dc:Bounds x="250" y="60" width="100" height="80"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_di" bpmnElement="End">
        <dc:Bounds x="420" y="80" width="36" height="36"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="f1_di" bpmnElement="f1">
        <di:waypoint x="186" y="98"/>
        <di:waypoint x="250" y="98"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="f2_di" bpmnElement="f2">
        <di:waypoint x="350" y="98"/>
        <di:waypoint x="420" y="98"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</definitions>
```

## Core Elements

### Events

```xml
<!-- Start events -->
<startEvent id="Start" name="Process Started"/>
<startEvent id="TimerStart" name="Scheduled">
  <timerEventDefinition/>
</startEvent>
<startEvent id="MsgStart" name="Message Received">
  <messageEventDefinition messageRef="Message_1"/>
</startEvent>

<!-- End events -->
<endEvent id="End" name="Process Complete"/>
<endEvent id="ErrorEnd" name="Failed">
  <errorEventDefinition errorRef="Error_1"/>
</endEvent>
<endEvent id="MsgEnd" name="Notify Customer">
  <messageEventDefinition messageRef="Message_2"/>
</endEvent>

<!-- Intermediate events -->
<intermediateCatchEvent id="Wait" name="Wait for Approval">
  <timerEventDefinition/>
</intermediateCatchEvent>
<intermediateThrowEvent id="Notify" name="Send Confirmation">
  <messageEventDefinition messageRef="Message_3"/>
</intermediateThrowEvent>
```

### Tasks

```xml
<task id="T1" name="Manual Task"/>
<userTask id="T2" name="Review Order"/>
<serviceTask id="T3" name="Call Payment API"/>
<scriptTask id="T4" name="Calculate Total"/>
<sendTask id="T5" name="Send Email"/>
<receiveTask id="T6" name="Wait for Payment"/>
<businessRuleTask id="T7" name="Apply Discount Rules"/>
<callActivity id="CA1" name="Sub-Process" calledElement="SubProcess_1"/>
```

### Gateways

```xml
<!-- Exclusive (XOR) — only one path taken -->
<exclusiveGateway id="GW1" name="Order Valid?"/>

<!-- Parallel (AND) — all paths taken simultaneously -->
<parallelGateway id="GW2" name="Fork"/>

<!-- Inclusive (OR) — one or more paths taken -->
<inclusiveGateway id="GW3" name="Select Channels"/>

<!-- Event-based — wait for one of several events -->
<eventBasedGateway id="GW4"/>
```

Conditional sequence flows:
```xml
<sequenceFlow id="f_yes" sourceRef="GW1" targetRef="Task_Process" name="Yes">
  <conditionExpression>${orderValid == true}</conditionExpression>
</sequenceFlow>
<sequenceFlow id="f_no"  sourceRef="GW1" targetRef="Task_Reject" name="No"/>
```

Mark the default flow:
```xml
<exclusiveGateway id="GW1" name="Check" default="f_default"/>
```

### Pools and Lanes (Collaboration)

```xml
<collaboration id="Collab_1">
  <participant id="Pool_Customer" name="Customer" processRef="Process_Customer"/>
  <participant id="Pool_Company"  name="Company"  processRef="Process_Company"/>
  <messageFlow id="mf1" sourceRef="Task_SendOrder" targetRef="Start_Receive"/>
</collaboration>

<process id="Process_Company">
  <laneSet>
    <lane id="Lane_Sales" name="Sales">
      <flowNodeRef>Task_Validate</flowNodeRef>
    </lane>
    <lane id="Lane_Finance" name="Finance">
      <flowNodeRef>Task_Invoice</flowNodeRef>
    </lane>
  </laneSet>
  <!-- tasks and flows -->
</process>
```

### Sub-Processes

```xml
<subProcess id="SP1" name="Handle Payment" triggeredByEvent="false">
  <startEvent id="SPStart"/>
  <task id="SPTask1" name="Charge Card"/>
  <endEvent id="SPEnd"/>
  <sequenceFlow id="spf1" sourceRef="SPStart" targetRef="SPTask1"/>
  <sequenceFlow id="spf2" sourceRef="SPTask1" targetRef="SPEnd"/>
</subProcess>
```

## Complete Example: Order Processing

```xml
<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
             xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
             xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
             targetNamespace="http://example.com">

  <process id="OrderProcess" name="Order Processing" isExecutable="false">

    <startEvent id="Start" name="Order Received"/>
    <sequenceFlow id="f1" sourceRef="Start" targetRef="ValidateTask"/>

    <userTask id="ValidateTask" name="Validate Order"/>
    <sequenceFlow id="f2" sourceRef="ValidateTask" targetRef="ValidGW"/>

    <exclusiveGateway id="ValidGW" name="Order Valid?" default="f_invalid"/>
    <sequenceFlow id="f_valid"   sourceRef="ValidGW" targetRef="PaymentTask" name="Yes">
      <conditionExpression>${valid}</conditionExpression>
    </sequenceFlow>
    <sequenceFlow id="f_invalid" sourceRef="ValidGW" targetRef="RejectEnd" name="No"/>

    <serviceTask id="PaymentTask" name="Process Payment"/>
    <sequenceFlow id="f3" sourceRef="PaymentTask" targetRef="PayGW"/>

    <exclusiveGateway id="PayGW" name="Payment OK?" default="f_failed"/>
    <sequenceFlow id="f_ok"     sourceRef="PayGW" targetRef="FulfillTask" name="Yes">
      <conditionExpression>${paid}</conditionExpression>
    </sequenceFlow>
    <sequenceFlow id="f_failed" sourceRef="PayGW" targetRef="FailEnd" name="No"/>

    <task id="FulfillTask" name="Fulfill Order"/>
    <sequenceFlow id="f4" sourceRef="FulfillTask" targetRef="NotifyTask"/>

    <sendTask id="NotifyTask" name="Send Confirmation"/>
    <sequenceFlow id="f5" sourceRef="NotifyTask" targetRef="SuccessEnd"/>

    <endEvent id="SuccessEnd" name="Order Complete"/>
    <endEvent id="RejectEnd"  name="Order Rejected"/>
    <endEvent id="FailEnd"    name="Payment Failed">
      <errorEventDefinition/>
    </endEvent>

  </process>

  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane bpmnElement="OrderProcess">
      <bpmndi:BPMNShape bpmnElement="Start"        id="Start_di">        <dc:Bounds x="152" y="82"  width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="ValidateTask" id="ValidateTask_di"> <dc:Bounds x="240" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="ValidGW"      id="ValidGW_di">      <dc:Bounds x="395" y="75"  width="50"  height="50"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="PaymentTask"  id="PaymentTask_di">  <dc:Bounds x="500" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="PayGW"        id="PayGW_di">        <dc:Bounds x="655" y="75"  width="50"  height="50"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="FulfillTask"  id="FulfillTask_di">  <dc:Bounds x="760" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="NotifyTask"   id="NotifyTask_di">   <dc:Bounds x="920" y="60"  width="100" height="80"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="SuccessEnd"   id="SuccessEnd_di">   <dc:Bounds x="1082" y="82" width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="RejectEnd"    id="RejectEnd_di">    <dc:Bounds x="397" y="200" width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNShape bpmnElement="FailEnd"      id="FailEnd_di">      <dc:Bounds x="657" y="200" width="36"  height="36"/></bpmndi:BPMNShape>
      <bpmndi:BPMNEdge bpmnElement="f1"       id="f1_di">       <di:waypoint x="188" y="100"/> <di:waypoint x="240" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f2"       id="f2_di">       <di:waypoint x="340" y="100"/> <di:waypoint x="395" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_valid"  id="f_valid_di">  <di:waypoint x="445" y="100"/> <di:waypoint x="500" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_invalid" id="f_invalid_di"><di:waypoint x="420" y="125"/> <di:waypoint x="420" y="218"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f3"       id="f3_di">       <di:waypoint x="600" y="100"/> <di:waypoint x="655" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_ok"     id="f_ok_di">     <di:waypoint x="705" y="100"/> <di:waypoint x="760" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f_failed" id="f_failed_di"> <di:waypoint x="680" y="125"/> <di:waypoint x="680" y="218"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f4"       id="f4_di">       <di:waypoint x="860" y="100"/> <di:waypoint x="920" y="100"/></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge bpmnElement="f5"       id="f5_di">       <di:waypoint x="1020" y="100"/><di:waypoint x="1082" y="100"/></bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</definitions>
```

## Tips

- Every element needs a unique `id` — use descriptive names, not `sid-xxx`
- The `<BPMNDiagram>` section controls layout — without it Kroki can't render
- Layout coordinates are in pixels; 100x80 is a standard task size, 36x36 for events, 50x50 for gateways
- `default` attribute on gateways marks the fallback path (rendered with a slash)
- For complex diagrams, generate the XML from a BPMN editor (Camunda Modeler, bpmn.io) then render with Kroki

## See Also
- `plantuml` skill — sequence and activity diagrams (simpler syntax)
- `mermaid-diagrams` skill — flowcharts and sequence diagrams

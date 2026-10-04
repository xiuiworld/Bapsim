# A03 — 센서 없는 Matter 연결 검증

진행 시작: 2026-10-04. A02의 실제 IPv6/Thread 상태 수신 검증을 이 단계에서 함께 수행한다. HA Cloud 외부 접속은 로컬 시험의 선행 조건이 아니다.

## 선택한 시험 구성

- SDK: nRF Connect SDK 3.4.1 LTS, 툴체인 `4f5b6ad6dd`.
- 보드: nRF54LM20-DK / PCA10184 / 하드웨어 리비전 0.7.0.
- 빌드 타깃: `nrf54lm20dk/nrf54lm20b/cpuapp`.
- SDK 공식 예제: `C:\ncs\v3.4.1\nrf\samples\matter\contact_sensor`.
- 해당 예제의 `sample.yaml`에서 이 보드 타깃 지원을 확인했다.
- SDK 원본 예제를 사용한다. 프로젝트 `firmware/` 수정은 없다.
- HA: `http://192.168.1.13`, Matter Server 설치 완료.
- Thread: Nest Hub 2세대, HA 선호 네트워크 `Google-17C0`.
- HA 네트워크 화면에서 IPv6 `자동` 활성화와 ULA 주소(`fdee:196d:1c36:17c0:…/64`) 할당을 확인했다. 이는 실제 보드와의 통신 성공을 대신하지 않는다.

## 버튼으로 만들 시험 입력

SDK README와 구현에 따르면 nRF54 DK의 **Button 1**을 누르면 접촉 감지, 떼면 접촉 미감지가 된다. Endpoint 1의 Boolean State 클러스터 값을 바꾼다. LED 1은 접촉 감지 상태를 표시한다.

이것은 시험용 접촉 상태이며 실제 밥솥 뚜껑 센서 측정이 아니다. HA에서 표시되는 열림/닫힘의 방향은 등록 후 실제 버튼 동작과 대조한다.

미등록 상태에서 **Button 0을 짧게 누르면** Bluetooth 등록 광고를 시작할 수 있다. 길게 누르면 초기화 동작이므로 등록 안내에서는 짧게 누른다.

## Google Home 개발 설정

개발용 기기는 Google Home Developer Console에 펌웨어와 같은 VID/PID의 Matter 통합을 만들고, 해당 프로젝트 구성원 계정으로 등록해야 한다.

- 시험 VID: `0xFFF1`.
- 예제 PID: `0x8000` (32768).
- 실제 빌드 설정과 등록 코드는 빌드 완료 후 재확인한다.
- Nest를 등록한 Google 계정으로 개발자 콘솔에 로그인한다.
- 사용자 로그인 후 `Bapsim` 프로젝트(`bapsim-006d3b`)를 생성했다.
- `Bapsim Contact Sensor`, 유형 `Sensor`, VID `0xfff1`, PID `0x8000`이 프로젝트 목록에 저장된 것을 확인했다. 실제 기기 등록 성공과는 별개다.

근거: [Google 개발 기기 등록 조건](https://developers.home.google.com/matter/get-started), [프로젝트 생성](https://developers.home.google.com/matter/project/create).

## 진행 상태

- [x] 보드 지원 예제와 버튼 동작 확인.
- [x] 예제 빌드 완료·플래시·부팅 로그 확인.
- [x] Google Home 개발자 프로젝트와 VID/PID 설정.
- [x] Google Home 등록 성공(2026-10-04 사용자 확인).
- [x] HA에 공유 후 초기 상태 수신 확인(기기 1개, 문: 열림).
- [x] 버튼 입력 변화와 HA 표시 대조.
- [x] 보드 재부팅 후 재연결·상태 수신 확인.

전체 sysbuild가 종료 코드 0으로 완료됐다. MCUboot·factory data·Contact Sensor 세 이미지를 쓰기·검증하고 재부팅했다. COM8 / 115200 baud에서 아래 로그를 확인했다.

```text
OpenThread started: OK
Server Listening...
Vendor Id: 65521 (0xFFF1)
Product Id: 32768 (0x8000)
Product Name: Matter Contact Sensor
CHIPoBLE advertising started
NFC Tag emulation started
```

본체 메모리 사용량은 Flash 753,428 B, RAM 171,996 B다. 부트로더는 별도로 Flash 39,680 B, RAM 25,896 B다. 본체의 Hardware Version 0은 예제의 Matter 속성이며 PCB 리비전 0.7.0과 다르다.

미등록 부팅 로그에는 DNS-SD 서비스 광고 실패 메시지도 있었다. BLE 광고 시작은 확인했으나, Thread 가입 후 서비스 발견 정상 여부는 실제 등록 시험에서 확인해야 한다. 이후 Google Home 등록 및 HA 공유에 성공했고 HA에서 초기 접촉 상태를 확인했다. 로컬 부팅 로그: `local-data/a03/boot.log`.

빌드 경로는 `%TEMP%\bapsim-a03-contact`다. 첫 구성은 VID를 `0xFFF1`로 전달해 Kconfig의 정수 형식 검사에서 중단됐다. 같은 값의 십진수 `65521`로 수정해 빌드에 성공했다.

재빌드의 생성된 `.config`에서 `CONFIG_CHIP_DEVICE_VENDOR_ID=65521`, `CONFIG_CHIP_DEVICE_PRODUCT_ID=32768`을 확인했다. SDK 버전 보고 단계는 첫 실행에 수분이 걸렸다. 기존 파일을 삭제하거나 SDK를 재설치할 오류는 아니었다.

## 실제 버튼·재연결 검증 결과

2026-10-04 KST 기준으로 사용자 실제 버튼 조작과 HA 웹 화면·Matter Server 로그를 대조했다.

| 입력 | Matter Boolean State | HA 표시 | 확인 근거 |
|---|---|---|---|
| Button 1 누름 | `true` | 문: 닫힘 | 16:06:08 상태 변경 이벤트 및 HA 화면 |
| Button 1 놓음 | `false` | 문: 열림 | 16:06:46 상태 변경 이벤트 및 HA 화면 |

실제 센서가 아닌 보드 버튼 시험이며, `Boolean State=true`를 내부 `lid_open=true`로 그대로 해석하면 방향이 반대다. 실제 센서 연결 시 극성을 다시 검증한다. Google Home 등록 유지는 사용자에게 확인받았고, Google Home 화면의 상태 변화는 직접 관찰하지 않았다.

등록 정보를 지우지 않는 소프트웨어 재부팅을 한 번 수행했다. `local-data/a03/reconnect.log`에서 Thread `CHILD`·`Thread Attached`·`Resuming 2 subscriptions` 및 HA에 대한 ReportData 전송과 성공 응답을 확인했다. HA 서버에서도 16:07:58 `Resumed session`, 16:08:02 `generalDiagnostics.bootReason`, `basicInformation.startUp`, Thread `connectionStatus: 0` 이벤트 수신을 확인했다. 짧은 재부팅 복구 시험이며 장기 안정성·전원 차단 시험까지 포함하지 않는다.

HA 기기 ID: `9f38f5d8ba55b2afe91ea0a44d6fa56d`. 로컬 증빙: `local-data/a03/ha-button-held.jpg`, `ha-button-released.jpg`, `reconnect.log`.

이 결과로 A02의 로컬 IPv6/Matter 상태 수신과 A03의 센서 없는 연결 시험을 완료했다. HA Cloud 외부 접속은 별도 미완료 항목이다.

## 재실행 명령

### Google Home → HA 공유 실패 진단 (2026-10-04)

사용자에게 Android 오류 코드 1이 표시됐다. Matter Server 9.2.0 로그에서 IPv6 UDP 연결, PASE 세션 수립 및 초기 속성 읽기는 성공했으나 `OperationalCredentials.DeviceAttestation` 단계에서 개발용 인증서 정책 때문에 거절됐음을 확인했다.

핵심 로그: `This device uses a test/development certificate. To commission it, enable the "Test DCL" option in the settings`.

앱 구성의 `Enable test-net DCL usage`는 꺼져 있었다. 이 옵션은 개발용 인증서 및 test-net DCL 기기 정보를 허용한다. 사용자 승인 후 `enable_test_net_dcl: true`를 저장하고 Matter Server를 재시작했다. 페이지 새로 고침 후에도 옵션이 켜져 있음을 확인했다. 2026-10-04 15:30 KST 시작 로그에서 test-net DCL 활성화와 HA WebSocket 재연결을 확인했다. 이후 사용자 재시도로 HA 추가에 성공했다. Google Home 등록도 유지됐다는 사용자 확인을 받았고, HA 웹 화면에서 Matter Contact Sensor 1개·9개 엔티티 및 문: 열림 상태를 확인했다. 버튼 변화와 재부팅 후 수신도 아래 기록과 같이 검증했다.

### 빌드·설치

SDK 3.4.1 대응 툴체인이 적용된 터미널에서 실행한다.

```powershell
Set-Location C:\ncs\v3.4.1
west build --sysbuild -b nrf54lm20dk/nrf54lm20b/cpuapp C:\ncs\v3.4.1\nrf\samples\matter\contact_sensor -d "$env:TEMP\bapsim-a03-contact" -- -DCONFIG_CHIP_DEVICE_VENDOR_ID=65521
# 빌드 성공 후, 장치 ID를 nrfutil device list로 조회하여 사용한다.
west flash -d "$env:TEMP\bapsim-a03-contact" --no-rebuild --dev-id <장치_ID>
```

등록 코드는 SDK README의 예시보다 빌드가 생성한 `factory_data.txt`와 실제 부팅 로그를 우선한다. QR 이미지는 `factory_data.png`다. 생성 파일에는 개발 기기의 등록 정보가 있으므로 로컬에 둔다.

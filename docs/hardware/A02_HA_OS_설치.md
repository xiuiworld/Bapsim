# A02 HA OS 설치 준비 — 2026-10-04

## 선택한 설치 방식

Windows Hyper-V의 HA OS 가상 머신을 사용한다. 프로젝트 합의 및 HA의 Matter 권장 설치 경로에 따른다. Docker는 실행 확인했으나 기존 HA 컨테이너는 없었다. 다른 프로젝트의 Docker 컨테이너는 변경하지 않는다.

현재 프로세스는 Windows 관리자 권한이 없어 `Get-VM`과 `Get-VMSwitch` 조회부터 권한 거부됐다. Hyper-V 도구는 설치돼 있다. 기존 VM 목록과 서비스의 실제 준비 상태는 관리자 실행에서 추가 확인된다. 이 문제는 Codex 자동 승인 거절이 아닌 Windows 권한 제한이다.

## 준비한 이미지와 설치 스크립트

- 공식 최신 안정 릴리스 조회 결과: HA OS 18.3.
- 공식 이미지: https://github.com/home-assistant/operating-system/releases/download/18.3/haos_ova-18.3.vhdx.zip
- 배포 API SHA256: `f6218e9e32415c0d1ada87852a3ac2b16cdbc203481ee241b420c8de927b8d00`.
- 저장 위치: Git 제외 `local-data/haos/` (VM 운영 중 삭제하거나 이동하지 않는다).
- 다운로드·SHA256 일치·압축 해제 완료. VHDX 크기 1,216,348,160 bytes. VM 생성·기동은 아직 미실행.
- 스크립트: `tools/install_a_haos.ps1`.
- 설정: VM `Bapsim-HAOS`, 2세대, 2 vCPU, RAM 기본 2GiB, VM Secure Boot 해제. 첫 4GiB 기동은 메모리 부족으로 실패해 기본값을 조정했다.
- 현재 연결된 물리 어댑터: `이더넷` / Realtek USB 2.5GbE. Wi-Fi는 점검 당시 미연결.
- 같은 어댑터의 외부 스위치가 있으면 재사용하고, 없으면 `Bapsim-LAN`을 생성한다. 외부 스위치 생성 중 PC 네트워크가 잠시 끊길 수 있다.
- 기존 동명 VM이 있으면 변경하지 않고 상태만 출력한다. 설치 중 실패하면 로그를 먼저 확인하며 VM을 임의 삭제하지 않는다.

## 사용자에게 필요한 마지막 실행

시작 메뉴에서 **Windows PowerShell → 관리자 권한으로 실행** 후 다음을 실행한다. 이 명령의 실행 정책 옵션은 해당 프로세스에만 적용된다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\xiui\Documents\Projects\Bapsim\tools\install_a_haos.ps1"
```

스크립트는 Windows 재부팅을 실행하지 않는다. `local-data/haos/install.log`에 결과를 남긴다. Hyper-V 구성 오류나 권한 오류가 나면 로그를 확인해 해당 단계부터 진행한다.

기동 후 처음 준비에는 몇 분 이상 걸릴 수 있다. `http://homeassistant.local:8123`에 접속한다. 이름으로 접속되지 않으면 관리자 PowerShell에서 다음으로 IP를 확인해 `http://IP주소:8123`을 연다.

```powershell
Get-VMNetworkAdapter -VMName Bapsim-HAOS | Select-Object -ExpandProperty IPAddresses
```

IP가 아직 표시되지 않으면 Hyper-V 관리자에서 VM 콘솔을 확인한다. HA 초기 관리자 계정의 비밀번호는 사용자가 직접 입력한다.

## 이후 A02 작업

### 최초 기동 메모리 부족 복구

사용자 실행에서 VM/외부 스위치 생성 후 4GiB 할당 오류 `0x800705AA`로 기동에 실패했다. 이후 OS 조회의 여유 물리 메모리는 약 3.62GiB였다. 관리자 PowerShell에서 다음으로 기존 VM의 메모리를 2GiB로 낮춰 시작한다. 실제 기동 성공은 후속 확인 대상이다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\xiui\Documents\Projects\Bapsim\tools\install_a_haos.ps1" -ResumeExisting -MemoryGB 2
```

`-ResumeExisting`은 연결 디스크 또는 그 차등 디스크의 부모 체인이 설치 원본 VHDX로 연결되는지 `Get-VHD`로 확인한 뒤 정지 상태 VM만 수정한다. 실행 중이면 상태만 표시한다. 가상 디스크·네트워크를 다시 만들지 않는다. 여전히 부족하면 사용하지 않는 앱을 닫고 재시도한다. Docker 종료는 사용자가 다른 작업의 사용 여부를 확인한 뒤 결정한다.

첫 재시도에서는 원본 경로만 비교하던 검사에 걸렸다. 로컬에 체크포인트와 AVHDX가 생성된 것을 확인해 부모 체인 검증으로 수정했다. 문법 검사와 모의 원본/차등/무관 디스크 검사는 통과했으며 실제 부모 체인과 VM 기동은 관리자 재실행으로 확인해야 한다. 체크포인트를 삭제하거나 디스크 연결을 바꾸지 않는다.

### 기동 성공 후

2026-10-04 자격 증명 후속: 사용자가 Android에서 자격 증명 보내기를 수행한 후, 데스크톱 Thread 화면을 새로고침하자 `선호 네트워크로 설정` 버튼이 나타났다. 이를 선택해 `Google-17C0`이 선호하는 네트워크로 이동한 것을 확인했다. 증거: `local-data/haos/thread-preferred.jpg`. 비밀 자격 증명 값은 열람·저장하지 않았다. 실제 보드의 Matter 등록·Thread 패킷 통신은 여전히 후속 검증 대상이다.

2026-10-04 Companion 후속: 사용자가 Android HA 앱 로그인·대시보드 표시 완료를 알렸고, HA UI에서도 Mobile App 1개 기기 등록을 확인했다. Thread 구성에 `Google-17C0`, 보더 라우터 1개, `Google Inc. Google Nest Hub #8333`이 표시된다. 아직 선호 네트워크는 없으며, 자격 증명 가져오기와 실제 Matter 기기 연결은 미검증이다. 발견 증거: `local-data/haos/thread-discovered.jpg`. 자격 증명은 휴대폰 앱과 HA 사이에서 전달하며 채팅/저장소에 복사하지 않는다.

2026-10-04 Nest 후속: 사용자가 중고 Nest Hub 초기화 후 Android Google Home 등록 완료를 알렸다. 사용할 폰은 사용자 진술 Nothing Phone (4a). HA 통합 목록에서 Thread 자동 발견을 확인하고 추가 성공했다. Thread 구성 화면은 아직 선호 네트워크와 기타 네트워크 목록 없이 표시돼 실제 Nest 네트워크·자격 증명·IPv6 통신은 미검증이다. 증거: `local-data/haos/thread-status.jpg`. 다음은 폰의 HA Companion 연결과 Nest/폰/HA의 LAN 확인이다. Google Nest 클라우드 통합이나 별도 USB용 OTBR는 설치하지 않았다.

2026-10-04 후속 UI 검증: 사용자 계정 로그인 후 `http://192.168.1.13/home/overview` 대시보드 확인. 현재 접속은 포트 없는 주소를 사용한다. 설정 → 통합 구성 요소 → Matter에서 공식 Matter Server Supervisor 애드온 옵션을 선택해 설치·시작을 진행했고, `성공 / Matter에 대한 구성을 만들었습니다`를 확인했다. 완료 후 Matter 허브와 기기 추가 버튼이 표시된다. 증거 화면은 Git 제외 `local-data/haos/matter-installed.jpg`. 실제 Matter 기기 등록과 Nest/Thread 통신은 아직 미검증이다.

사용자 Hyper-V 조회로 실제 VM IPv4 `192.168.1.13`을 확인했다. 직접 HTTP 점검에서 초기 8123 연결 거부 후 재확인 시 200과 onboarding HTML을 수신했다. Observer `http://192.168.1.13:4357`도 200이며 `Supervisor: Connected`, `Support: Supported`, `Health: Healthy`였다. 접속 주소는 `http://192.168.1.13:8123`을 사용한다. DHCP 주소이므로 향후 변경될 수 있다. 초기 사용자 계정 생성과 Matter 설치는 다음 단계다.

2026-10-04 사용자 관리자 실행 로그에서 부모 디스크 검증과 RAM 2GiB 기동 성공, `Bapsim-HAOS Running`을 확인했다. 초기 IP 목록은 비어 있었다. 이후 `homeassistant.local`은 `218.38.137.28`로 해석되며 HA HTML을 반환했지만, 호스트의 Bapsim-LAN은 `192.168.1.8/게이트웨이 192.168.1.1`이었다. 이름 응답만으로 이번 VM의 웹 화면이라고 확정하지 않는다. VM 콘솔 또는 Hyper-V의 실제 IP를 확인한 뒤 직접 접속한다. Matter 설치·계정 설정은 아직 미확인이다.

1. HA 첫 화면과 계정 설정 완료.
2. Matter 통합 및 공식 Matter Server 앱 설치·기동 확인.
3. Nest와 HA 사이 IPv6·mDNS 및 같은 가정 LAN 연결 확인.
4. Companion 앱 연결. Cloud 체험은 발표 일정과 기간을 확인한 뒤 시작.

HA OS VM 기동만으로 Matter/Thread 장치 연결 성공을 뜻하지 않는다. Hyper-V는 일반 USB 전달을 지원하지 않으나, 이번에는 Nest를 Thread Border Router로 쓰고 보드 USB 빌드·플래시는 Windows에서 수행한다.

참고: [HA Windows 설치](https://www.home-assistant.io/installation/windows/), [HA Matter 안내](https://www.home-assistant.io/integrations/matter/).

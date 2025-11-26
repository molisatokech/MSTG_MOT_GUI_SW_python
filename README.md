# MSTG MOT GUI SW (Python)

이 저장소는 MSTG 드라이브 모듈을 제어하고 부트로더 업데이트를 수행하는 PyQt5 기반 GUI 애플리케이션입니다. CAN 장치(Kvaser/PCAN)와 연결하여 DBC 파일을 로드하고, 실시간 메시지 모니터링/그래프화, 제어 명령 송신, 펌웨어 업데이트 등의 기능을 제공합니다.

## 주요 구성 요소
- `main.py`: 앱 진입점. `MainWindow`를 생성하고 우선순위/이벤트 루프를 설정합니다.
- `main_window.py`: PyQt5 UI와 각 위젯, 버튼 핸들러를 정의합니다.
- `main_window_logic.py`: DBC 파싱, 메시지 송수신, 그래프/데이터 갱신 등 비즈니스 로직이 있습니다.
- `can_receiver.py`: 별도 스레드에서 CAN 메시지를 수신해 UI에 전달합니다.
- `bootloader_update.py`: HEX 파일 기반 펌웨어/부트로더 업데이트 상태 머신.
- `custom_viewbox.py`: 그래프 확대/축소 커스텀 ViewBox.
- `dist/*.dbc`: 실행 시 사용할 수 있는 기본 DBC 예시 파일.

## 실행 환경
- Python 3.10 이상 권장
- 필수 패키지: PyQt5, python-can, cantools, pyqtgraph, intelhex, psutil 등 (`requirements.txt` 참고)
- 지원 장치: Kvaser, PCAN (python-can 설정 필요)

## 빠른 시작
1. 가상환경 준비
   ```powershell
   cd MSTG_GUI_Project
   python -m venv .venv
   .\.venv\Scripts\activate
   ```
2. 의존성 설치
   ```powershell
   pip install -r requirements.txt
   ```
3. 애플리케이션 실행
   ```powershell
   python main.py
   ```
4. GUI에서 CAN 장치를 선택하고 `Load DBC File`로 필요한 DBC를 로드한 뒤 메시지를 전송/그래프 확인/스캔 기능을 사용할 수 있습니다.

## Git 사용 팁
- `.venv/`, `__pycache__/` 등 임시 파일은 `.gitignore`에 포함되어 있습니다.
- 배포용 실행 파일(`*.exe`, `*.zip`)은 기본적으로 추적하지 않습니다. 필요한 산출물만 수동으로 추가하세요.

## 라이선스
프로젝트 내 DBC/HEX 파일 등은 사내 자산일 수 있으므로 외부 배포 시 주의하세요. 별도의 라이선스가 없다면 내부 전용으로 유지하십시오.
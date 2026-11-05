# 판매를 켤 때 다시 넣을 문단 (2026-11-05 판매 끄기 모드에서 뺀 원문)

각 블록을 해당 파일의 같은 위치(개인정보: '알림' 다음, 지원: FAQ 맨 앞)에 되돌린다.

## privacy/index.html

```html
<h2>앱 안 결제(KERB Pro)</h2>
<p>KERB Pro는 앱 안에서 한 번 결제하는 상품입니다. 앱 안 결제는 Apple이 처리하며, 개발자는 카드 번호 등 결제 정보를 받지 않습니다. 구매 여부는 Apple의 구매 기록(StoreKit)으로 기기에서 확인하고, 이 확인 결과를 서버로 보내지 않습니다.</p>

```

## privacy/index.html

```html
<h2>In-app purchase (KERB Pro)</h2>
<p>KERB Pro is a one-time in-app purchase. In-app payments are processed by Apple, and the developer does not receive your payment information such as card numbers. Whether you own Pro is checked on your device through Apple's purchase records (StoreKit), and the result is not sent to any server.</p>

```

## support/index.html

```html
<p><strong>구매를 복원하려면 어떻게 하나요?</strong><br>
앱의 설정 → KERB Pro 화면에서 "구매 복원"을 누르세요. 구매할 때 쓴 Apple 계정으로 로그인돼 있어야 합니다. 새 기기나 앱을 다시 설치한 경우에도 같은 방법입니다.</p>
```

## support/index.html

```html
<p><strong>How do I restore my purchase?</strong><br>
Open Settings → KERB Pro in the app and tap "Restore purchases". You need to be signed in with the Apple account you used to buy it. The same works on a new device or after reinstalling.</p>
```


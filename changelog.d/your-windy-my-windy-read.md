- **Your Windy can show the owner's plan and platforms (still off: needs WINDY_YOUR_WINDY=1 and WINDY_MY_WINDY=1).**
  The agent reads its owner's Windy from Windy Accounts' one "my Windy" read (my-windy.v1): which platforms the owner
  has turned on, which they can turn on, and which are in preview or coming soon. It signs in with its own paired key
  when it has one, otherwise with its stored passport token. If Windy Accounts refuses, does not answer, or answers
  anything outside the agreed shape, those lines are simply left out, never guessed.

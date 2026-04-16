# Keep classes/methods used by reflection for future websocket/JSON parsing.
-keepclassmembers class * {
    public <init>(...);
}

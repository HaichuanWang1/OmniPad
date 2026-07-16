package com.omnipad.client.network

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

data class RecentHost(val host: String, val port: Int, val timestamp: Long)

class RecentHostsStore(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("omnipad_hosts", Context.MODE_PRIVATE)

    private fun loadAll(): List<RecentHost> {
        val json = prefs.getString("hosts", "[]") ?: "[]"
        val arr = JSONArray(json)
        return (0 until arr.length()).map { i ->
            val obj = arr.getJSONObject(i)
            RecentHost(
                host = obj.getString("host"),
                port = obj.getInt("port"),
                timestamp = obj.getLong("ts"),
            )
        }.sortedByDescending { it.timestamp }
    }

    private fun saveAll(hosts: List<RecentHost>) {
        val arr = JSONArray()
        hosts.forEach { h ->
            arr.put(JSONObject().apply {
                put("host", h.host)
                put("port", h.port)
                put("ts", h.timestamp)
            })
        }
        prefs.edit().putString("hosts", arr.toString()).apply()
    }

    fun get(): List<RecentHost> = loadAll()

    fun add(host: String, port: Int) {
        val list = loadAll().toMutableList()
        list.removeAll { it.host == host && it.port == port }
        list.add(0, RecentHost(host, port, System.currentTimeMillis()))
        saveAll(list.take(10))
    }

    fun remove(host: String, port: Int) {
        val list = loadAll().toMutableList()
        list.removeAll { it.host == host && it.port == port }
        saveAll(list)
    }
}

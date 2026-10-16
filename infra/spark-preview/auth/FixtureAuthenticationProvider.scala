package fixture

import java.nio.charset.StandardCharsets.UTF_8
import java.nio.file.{Files, Paths}
import java.security.MessageDigest
import java.util.Properties
import javax.security.sasl.AuthenticationException
import org.apache.hive.service.auth.PasswdAuthenticationProvider

/** One ephemeral acceptance identity. This does not implement authorization. */
class FixtureAuthenticationProvider extends PasswdAuthenticationProvider {
  override def Authenticate(user: String, password: String): Unit = {
    if (user == null || password == null) {
      throw new AuthenticationException("Invalid fixture credentials")
    }
    val identity = new Properties()
    val input = Files.newInputStream(Paths.get("/fixture/identity.properties"))
    try identity.load(input) finally input.close()
    val digest = MessageDigest.getInstance("SHA-256")
      .digest((identity.getProperty("salt") + password).getBytes(UTF_8))
      .map(b => f"${b & 0xff}%02x").mkString
    val validUser = MessageDigest.isEqual(user.getBytes(UTF_8), identity.getProperty("username").getBytes(UTF_8))
    val validPassword = MessageDigest.isEqual(digest.getBytes(UTF_8), identity.getProperty("digest").getBytes(UTF_8))
    if (!(validUser & validPassword)) {
      throw new AuthenticationException("Invalid fixture credentials")
    }
  }
}
